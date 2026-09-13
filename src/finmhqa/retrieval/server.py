"""FastAPI factory; explicit startup and one local inference request at a time."""
from contextlib import asynccontextmanager
import os
from typing import Literal
import threading
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Query(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    query:str=Field(min_length=1,max_length=2000)
    mode:Literal['dense','bm25','hybrid']='hybrid'
    top_k:int=Field(default=5,ge=1,le=20)
    company:str|None=Field(default=None,min_length=1,max_length=80)
    expand_parent:bool=True
    n_cand:int=Field(default=30,ge=1,le=100)

    @model_validator(mode='after')
    def bounds(self):
        if not self.query.strip() or self.n_cand<self.top_k:raise ValueError('Blank query or n_cand below top_k')
        return self


def create_app(retriever=None):
    @asynccontextmanager
    async def lifespan(app):
        models=None
        try:
            if retriever is None:
                from .model_process import ModelProcess
                from .retriever import HybridRetriever
                index=os.environ.get('FINMHQA_INDEX','index')
                models=ModelProcess(os.environ.get('FINMHQA_MODELS','data/models/retrieval'),
                    max_length=int(os.environ.get('FINMHQA_MAX_LENGTH','1024')),
                    batch_size=int(os.environ.get('FINMHQA_BATCH_SIZE','4')))
                app.state.retriever=HybridRetriever(index,models,allow_experimental=os.environ.get('FINMHQA_EXPERIMENTAL')=='1')
            else:app.state.retriever=retriever
            yield
        finally:
            app.state.retriever=None
            if models is not None:models.close()
    app=FastAPI(title='FinMHQA Retrieval',version='0.2.0',lifespan=lifespan)
    capacity=threading.BoundedSemaphore(1)

    @app.get('/health')
    def health():
        r=getattr(app.state,'retriever',None)
        if r is None or not getattr(r.models,'alive',True):raise HTTPException(503,'Index or inference worker not ready')
        return dict(status='ready',count=r.manifest['count'],admission=r.manifest['admission'],model_bundle_sha256=r.manifest['model_bundle_sha256'])

    @app.post('/search')
    def search(request:Query):
        r=getattr(app.state,'retriever',None)
        if r is None or not getattr(r.models,'alive',True):raise HTTPException(503,'Index or inference worker not ready')
        if not capacity.acquire(blocking=False):raise HTTPException(503,'Inference busy; retry later')
        try:
            results=r.search(**request.model_dump())
            return dict(results=results,index_admission=r.manifest['admission']['status'],
                        dataset_sha256=r.manifest['admission']['dataset_sha256'],
                        primary_count=sum(not item['attached'] for item in results))
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        finally:capacity.release()
    return app
