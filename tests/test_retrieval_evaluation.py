"""Frozen-set acceptance and durable failure evidence; no models required."""
import importlib.util
import json
from pathlib import Path
import pytest

httpx=pytest.importorskip('httpx')
spec=importlib.util.spec_from_file_location('retrieval_evaluation',Path(__file__).parents[1]/'scripts/evaluate_retrieval.py')
evaluation=importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


@pytest.fixture
def frozen(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('configs').mkdir()
    source=Path('configs/retrieval_dev30.jsonl')
    rows=[dict(query_id=f'q{i}',query=str(i),dataset_sha256='corpus',gold_chunk_ids=[f'gold{i}']) for i in range(30)]
    source.write_text(''.join(json.dumps({k:r[k] for k in ('query_id','query')})+'\n' for r in rows))
    bound=Path('bound.jsonl');bound.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    bound.with_suffix('.summary.json').write_text(json.dumps(dict(bound_sha256=evaluation.sha256(bound),
        query_source_sha256=evaluation.sha256(source),dataset_sha256='corpus')))
    return bound


def mock_api(monkeypatch,hits=30,admission='ACCEPTED',fail_on=None):
    original=httpx.Client
    calls=[]
    def respond(request):
        if request.url.path=='/health':
            return httpx.Response(200,json=dict(admission=dict(status=admission,dataset_sha256='corpus')))
        body=json.loads(request.content);i=int(body['query']);calls.append(body)
        if len(calls)==fail_on:return httpx.Response(500,text='injected failure')
        # An attached gold must not rescue a failed primary Top-5.
        results=[dict(chunk_id=f'gold{i}' if i<hits else 'miss',attached=False),dict(chunk_id=f'gold{i}',attached=True)]
        return httpx.Response(200,json=dict(dataset_sha256='corpus',results=results))
    monkeypatch.setattr(evaluation.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    return calls


@pytest.mark.parametrize('hits,status,expected',[(26,'ACCEPTED','PASSED'),(25,'ACCEPTED','FAILED'),(30,'EXPERIMENTAL_UNREVIEWED','DIAGNOSTIC_ONLY')])
def test_frozen_acceptance_excludes_attachments(frozen,monkeypatch,hits,status,expected):
    calls=mock_api(monkeypatch,hits,status)
    report=evaluation.evaluate('http://test',frozen,'result.json')
    assert report['acceptance']==expected
    assert report['by_mode']['hybrid']['hits']==hits
    assert len(calls)==90 and all(c['n_cand']==30 for c in calls)
    assert len(Path('result.requests.jsonl').read_text().splitlines())==90


def test_failure_keeps_responses_and_refuses_overwrite(frozen,monkeypatch):
    mock_api(monkeypatch,fail_on=2)
    with pytest.raises(httpx.HTTPStatusError):evaluation.evaluate('http://test',frozen,'result.json')
    journal=Path('result.requests.jsonl')
    before=journal.read_bytes()
    assert [json.loads(l)['status_code'] for l in before.splitlines()]==[200,500]
    assert not Path('result.json').exists()
    with pytest.raises(FileExistsError):evaluation.evaluate('http://test',frozen,'result.json')
    assert journal.read_bytes()==before


def test_changed_binding_cannot_pass(frozen,monkeypatch):
    mock_api(monkeypatch)
    # Change serialization without changing question semantics or evidence IDs.
    rows=[json.loads(l) for l in frozen.read_text().splitlines()]
    frozen.write_text(''.join(json.dumps(r,separators=(',',':'))+'\n' for r in rows))
    report=evaluation.evaluate('http://test',frozen,'result.json')
    assert report['acceptance']=='DIAGNOSTIC_ONLY'
    assert not report['fixed_binding_verified']


def test_changed_question_cannot_be_laundered_by_updated_sidecar(frozen,monkeypatch):
    mock_api(monkeypatch)
    rows=[json.loads(l) for l in frozen.read_text().splitlines()];rows[0]['query']='1'
    frozen.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    sidecar=frozen.with_suffix('.summary.json');binding=json.loads(sidecar.read_text())
    binding['bound_sha256']=evaluation.sha256(frozen);sidecar.write_text(json.dumps(binding))
    report=evaluation.evaluate('http://test',frozen,'result.json')
    assert report['acceptance']=='DIAGNOSTIC_ONLY'


@pytest.mark.parametrize('tampered',[False,True])
def test_v2_requires_registered_source_review(frozen,monkeypatch,tampered):
    mock_api(monkeypatch)
    rows=[dict(json.loads(l),label_version='source_equivalence_v2') for l in frozen.read_text().splitlines()]
    frozen.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    review=Path('review.json');review.write_text('{"approved":"source-reviewed"}')
    binding_path=frozen.with_suffix('.summary.json');binding=json.loads(binding_path.read_text())
    binding.update(label_version='source_equivalence_v2',status='FROZEN',bound_sha256=evaluation.sha256(frozen),review_path=str(review),review_sha256=evaluation.sha256(review))
    binding_path.write_text(json.dumps(binding))
    Path('configs/retrieval_label_versions.json').write_text(json.dumps({'source_equivalence_v2':binding}))
    if tampered:review.write_text('{"approved":"changed"}')
    report=evaluation.evaluate('http://test',frozen,'result.json')
    assert report['acceptance']==('DIAGNOSTIC_ONLY' if tampered else 'PASSED')
