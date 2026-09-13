"""Bounded local IPC keeps conflicting Torch and FAISS OpenMP runtimes apart."""
import json
from pathlib import Path
import selectors
import subprocess
import sys
import threading
import numpy as np


class ModelProcess:
    def __init__(self,root,*,batch_size=4,max_length=1024,timeout=300):
        self.timeout=timeout;self.lock=threading.Lock()
        self.worker=subprocess.Popen([sys.executable,'-m','finmhqa.retrieval.model_worker',
            '--root',str(Path(root).resolve()),'--batch-size',str(batch_size),'--max-length',str(max_length)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1)
        self.selector=selectors.DefaultSelector();self.selector.register(self.worker.stdout,selectors.EVENT_READ)
        self.last_embed_truncated=[];self.last_rerank_truncated=[]
        try:
            ready=self._read()
            self.dimension=ready['dimension'];self.max_length=ready['max_length'];self.bundle_hash=ready['bundle_hash']
        except BaseException:
            self.close();raise

    @property
    def alive(self):return self.worker.poll() is None

    def _read(self):
        if not self.selector.select(self.timeout):
            self.close();raise RuntimeError('Local inference timed out; worker stopped')
        line=self.worker.stdout.readline()
        if not line:raise RuntimeError(f'Local inference worker exited ({self.worker.poll()})')
        response=json.loads(line)
        if not response.get('ok'):raise ValueError(response.get('error','Local inference failed'))
        return response

    def _request(self,payload):
        with self.lock:
            if not self.alive:raise RuntimeError('Local inference worker is not running')
            self.worker.stdin.write(json.dumps(payload,ensure_ascii=False,allow_nan=False)+'\n')
            self.worker.stdin.flush()
            return self._read()

    def encode(self,texts):
        result=self._request(dict(op='encode',texts=list(texts)))
        self.last_embed_truncated=result['truncated']
        return np.asarray(result['values'],dtype='float32').reshape(len(texts),self.dimension)

    def score(self,query,texts):
        result=self._request(dict(op='score',query=query,texts=list(texts)))
        self.last_rerank_truncated=result['truncated']
        return result['values']

    def close(self):
        if self.worker.stdin and not self.worker.stdin.closed:
            self.worker.stdin.close()
        if self.worker.poll() is None:
            try:self.worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.worker.terminate()
                try:self.worker.wait(timeout=3)
                except subprocess.TimeoutExpired:self.worker.kill();self.worker.wait(timeout=3)
        self.selector.close()
        for stream in (self.worker.stdin,self.worker.stdout):
            if stream:stream.close()

    def __enter__(self):return self
    def __exit__(self,*exc):self.close()
