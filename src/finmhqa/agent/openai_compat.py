"""OpenAI-compatible chat transport (e.g. OpenCode Zen). No tools are offered; the key never reaches disk."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from jsonschema import ValidationError, validate

REPAIR_PROMPT = ('你上一条输出不符合要求：{error}。请保持你原来的决定和内容不变，只修正格式，'
                 '重新输出一个完整、符合JSON Schema的JSON对象，不要任何其它文字。')


def load_env(path=Path('.env')):
    """Fill unset LLM_* variables from a git-ignored .env file; values are never printed or logged."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition('=')
        if sep and key.startswith('LLM_') and key not in os.environ:
            os.environ[key] = value.strip().strip('"\'')


def normalize_usage(raw):
    raw = raw or {}
    details = raw.get('prompt_tokens_details') or {}
    completion = raw.get('completion_tokens_details') or {}
    return {'input_tokens': int(raw.get('prompt_tokens') or 0),
            'cached_input_tokens': int(details.get('cached_tokens') or raw.get('prompt_cache_hit_tokens') or 0),
            'output_tokens': int(raw.get('completion_tokens') or 0),
            'reasoning_output_tokens': int(completion.get('reasoning_tokens') or 0)}


def normalized_events(response):
    """Deterministic Codex-shaped view of one raw chat completion, so existing audits can replay it."""
    message = response['choices'][0]['message']
    events = [{'type': 'thread.started', 'thread_id': response.get('id') or '', 'source': 'openai_compat_normalized'}]
    if message.get('reasoning_content'):
        events.append({'type': 'item.completed', 'item': {'type': 'reasoning', 'text': message['reasoning_content']}})
    if message.get('tool_calls'):
        events.append({'type': 'item.completed', 'item': {'type': 'tool_call', 'text': json.dumps(message['tool_calls'])}})
    events.append({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': strip_fence(message.get('content') or '')}})
    events.append({'type': 'turn.completed', 'usage': normalize_usage(response.get('usage'))})
    return events


def strip_fence(text):
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else ''
        text = text.rsplit('```', 1)[0]
    return text.strip()


class OpenAICompatTeacher:
    def __init__(self, base_url=None, model=None, api_key=None, max_attempts=2, post=None, transport_retries=2, backoff=5,
                 repair=False):
        load_env()
        self.base_url = (base_url or os.environ.get('LLM_BASE_URL', '')).rstrip('/')
        self.model = model or os.environ.get('LLM_MODEL_NAME', '')
        self._key = api_key or os.environ.get('LLM_API_KEY', '')
        if not self.base_url or not self.model:
            raise ValueError('LLM_BASE_URL and LLM_MODEL_NAME are required')
        if not self._key and post is None:
            raise ValueError('LLM_API_KEY is not set')
        self.max_attempts = max_attempts
        # repair=True: validate against the output schema and send the validation error back once (format only).
        # repair=False keeps the original behaviour: resend the identical request only when the reply is not JSON.
        self.repair = repair
        self.transport_retries, self.backoff = transport_retries, backoff
        self.transport_failures = []
        self._custom_post = post is not None
        self.post = post or self._post

    def _post(self, body, timeout, session=None):
        headers = {'Content-Type': 'application/json', 'User-Agent': 'fin-mhqa/0.1', 'Authorization': 'Bearer ' + self._key}
        if session:
            # OpenCode Go asks clients to send a stable per-conversation id for routing and prompt caching.
            headers['x-opencode-session'] = session
        request = urllib.request.Request(self.base_url + '/chat/completions', data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            error = RuntimeError(f'HTTP {exc.code}: {exc.read().decode(errors="replace")[:500]}')
            error.transient = exc.code == 429 or exc.code >= 500
            raise error from None

    def _send(self, body, timeout, log, session=None):
        # Network-level retry only (dropped connection, 429, 5xx); a model reply is never re-requested here.
        for attempt in range(self.transport_retries + 1):
            try:
                return self.post(body, timeout) if self._custom_post else self._post(body, timeout, session)
            except (OSError, http.client.HTTPException, RuntimeError, ValueError) as exc:
                # IncompleteRead (connection cut mid-body) is an HTTPException, not an OSError: retry it too.
                transient = isinstance(exc, (OSError, http.client.HTTPException)) or getattr(exc, 'transient', False)
                log.append({'attempt': attempt + 1, 'error': f'{type(exc).__name__}: {exc}'[:300], 'retried': transient and attempt < self.transport_retries})
                if not transient or attempt == self.transport_retries:
                    raise RuntimeError(f'teacher transport failed: {exc}') from None
                time.sleep(self.backoff * (attempt + 1))

    def decide(self, question, history, outdir, timeout=180, *, system, output_schema):
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=False)
        (outdir / 'schema.json').write_text(json.dumps(output_schema, ensure_ascii=False))
        prompt = system + '\n' + json.dumps({'question': question, 'history': history}, ensure_ascii=False)
        (outdir / 'input.txt').write_text(prompt)
        instruction = ('只输出一个JSON对象，不要Markdown代码块或其它文字。JSON必须满足以下JSON Schema：\n'
                       + json.dumps(output_schema, ensure_ascii=False))
        body = {'model': self.model, 'temperature': 0, 'response_format': {'type': 'json_object'},
                'messages': [{'role': 'system', 'content': instruction}, {'role': 'user', 'content': prompt}]}
        # One conversation = one question: every step_NN of the same question folder shares a session id.
        anchor = outdir.parent if outdir.name.startswith('step_') else outdir
        session = hashlib.sha256(str(anchor.resolve()).encode()).hexdigest()[:32]
        (outdir / 'request.json').write_text(json.dumps({**body, 'x-opencode-session': session}, ensure_ascii=False, indent=2))
        # Every attempt is kept; only a malformed reply is retried, never a valid but unwelcome answer.
        failure = None;rejected_usage = [];transport_log = [];repairs = []
        for attempt in range(self.max_attempts):
            folder = outdir if attempt == 0 else outdir / f'retry_{attempt}'
            folder.mkdir(exist_ok=True)
            if attempt:
                (folder / 'request.json').write_text(json.dumps({**body, 'x-opencode-session': session}, ensure_ascii=False, indent=2))
            start = time.monotonic()
            try:
                response = self._send(body, timeout, transport_log, session)
            finally:
                if transport_log:
                    (outdir / 'transport_errors.json').write_text(json.dumps(transport_log, ensure_ascii=False, indent=2))
            (folder / 'response.json').write_text(json.dumps(response, ensure_ascii=False, indent=2))
            events = normalized_events(response)
            with (folder / 'events.jsonl').open('x') as f:
                f.writelines(json.dumps(e, ensure_ascii=False) + '\n' for e in events)
            if any(e.get('item', {}).get('type') == 'tool_call' for e in events):
                raise RuntimeError('unapproved native item: tool_call')
            usage = events[-1]['usage']
            text = events[-2]['item']['text']
            try:
                action = json.loads(text)
                if self.repair:
                    validate(action, output_schema)
            except (json.JSONDecodeError, ValidationError) as exc:
                rejected_usage.append(usage)
                detail = exc.message if isinstance(exc, ValidationError) else f'不是合法JSON（{exc}）'
                failure = f'invalid output on attempt {attempt + 1}: {detail}'
                if self.repair:
                    where = '/'.join(map(str, exc.absolute_path)) if isinstance(exc, ValidationError) else ''
                    repairs.append({'attempt': attempt + 1, 'error': detail[:300], 'path': where})
                    body = {**body, 'messages': body['messages'][:2] + [
                        {'role': 'assistant', 'content': text},
                        {'role': 'user', 'content': REPAIR_PROMPT.format(error=(f'{where}: ' if where else '') + detail[:300])}]}
                continue
            (outdir / 'decision.json').write_text(json.dumps(action, ensure_ascii=False, indent=2))
            (outdir / 'usage.json').write_text(json.dumps(usage))
            (outdir / 'attempts.json').write_text(json.dumps({'attempts': attempt + 1, 'rejected_usage': rejected_usage,
                                                                'repairs': repairs, 'seconds': time.monotonic() - start}))
            if attempt:
                # Audits read the accepted attempt at the step root; earlier malformed replies stay in place.
                for name in ('response.json', 'events.jsonl'):
                    (outdir / name).rename(outdir / f'rejected_{name}')
                    (folder / name).rename(outdir / name)
            return action, usage
        raise RuntimeError(failure)
