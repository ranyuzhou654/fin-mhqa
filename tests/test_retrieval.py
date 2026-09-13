import json
from pathlib import Path
import numpy as np
import pytest

pytest.importorskip('faiss')
pytest.importorskip('bm25s')
from finmhqa.retrieval.build_index import build
from finmhqa.retrieval.retriever import HybridRetriever
from finmhqa.retrieval.artifacts import admission


class FakeModels:
    dimension=2;max_length=1024;bundle_hash='test-double'
    last_embed_truncated=[];last_rerank_truncated=[]
    def encode(self,texts):
        self.last_embed_truncated=[]
        return np.array([[.05,1] if '稀有' in t else [1,0] for t in texts],dtype='float32')
    def score(self,query,texts):
        self.last_rerank_truncated=[]
        return [1. if 'KV命中' in t else .3 for t in texts]


def chunk(i,text,company='常见公司',kind='text',part=None):
    c=dict(chunk_id=str(i),company=company,company_code='000001' if company=='常见公司' else '000002',
           source_sha256='source-'+company,section_path='财务',chunk_type=kind,page_start=1,page_end=1,text=text)
    if kind.startswith('table'):c.update(parent_table_id='shared-parent',part=part)
    return c


@pytest.fixture
def indexed(tmp_path,monkeypatch):
    dictionary=Path('configs/fin_dict.txt').resolve()
    monkeypatch.chdir(tmp_path)
    chunks=[chunk(i,'通用收入证据') for i in range(10)]
    chunks += [chunk(10,'稀有收入'),chunk(11,'稀有收入二','稀有公司')]
    chunks += [chunk(12,'完整表格'+'金融'*1000,'稀有公司','table_md',1),chunk(13,'KV命中 收益','稀有公司','table_kv',1),
               chunk(14,'同表不同分片','稀有公司','table_md',2),chunk(15,'不同公司的同名父表','常见公司','table_md',1)]
    source=Path('chunks.jsonl');source.write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in chunks))
    out=Path('data/experiments/test/index')
    models=FakeModels()
    build(source,out,models,dictionary=dictionary,experimental=True)
    return source,out,models


def test_rare_company_filter_is_complete(indexed):
    _,out,models=indexed
    r=HybridRetriever(out,models,allow_experimental=True)
    assert len(r.dense('通用',k=3,company='稀有公司'))==3
    assert all(r.chunks[i]['company']=='稀有公司' for i,_ in r.dense('通用',k=3,company='稀有公司'))
    assert r.search('通用',company='不存在')==[]
    assert r.sparse('zzzzunknown')==[]


def test_parent_expansion_and_full_evidence(indexed):
    _,out,models=indexed
    r=HybridRetriever(out,models,allow_experimental=True)
    result=r.search('收益',top_k=1,n_cand=16,company='000002')
    assert [c['chunk_id'] for c in result]==['13','12']
    assert result[1]['attached'] and len(result[1]['text'])>1500
    assert result[0]['source_sha256']=='source-稀有公司'
    assert len(r.search('收益',top_k=1,n_cand=16,company='000002',expand_parent=False))==1


def test_artifact_tamper_configuration_and_opt_in(indexed):
    _,out,models=indexed
    with pytest.raises(ValueError,match='explicit opt-in'):HybridRetriever(out,models)
    other=FakeModels();other.bundle_hash='different'
    with pytest.raises(ValueError,match='configuration differ'):HybridRetriever(out,other,allow_experimental=True)
    (out/'fin_dict.txt').write_text('changed')
    with pytest.raises(ValueError,match='integrity'):HybridRetriever(out,models,allow_experimental=True)


def test_admission_no_overwrite_and_rrf(indexed):
    source,out,models=indexed
    with pytest.raises(ValueError,match='requires M1'):admission(source,'index')
    with pytest.raises(ValueError,match='data/experiments'):admission(source,'index',experimental=True)
    with pytest.raises(FileExistsError):build(source,out,models,experimental=True)
    ranked=HybridRetriever.rrf([[(2,10),(1,5)],[(1,99),(3,90)]])
    assert ranked[0][0]==1
    qa=Path('qa');qa.mkdir()
    (qa/'audit.json').write_text(json.dumps({'structural_errors':[]}))
    (qa/'review_result.json').write_text(json.dumps({'status':'PENDING_REVIEW'}))
    with pytest.raises(ValueError,match='M1 acceptance'):admission(source,'index',qa)


def test_http_validation_and_real_indexes(indexed):
    from fastapi.testclient import TestClient
    from finmhqa.retrieval.server import create_app
    _,out,models=indexed
    r=HybridRetriever(out,models,allow_experimental=True)
    with TestClient(create_app(r)) as client:
        assert client.get('/health').json()['admission']['status']=='EXPERIMENTAL_UNREVIEWED'
        for request in ({'query':' '},{'query':'x','mode':'wrong'},{'query':'x','top_k':5,'n_cand':2},{'query':'x','top_k':'5'},{'query':'x','extra':'ignored?'}):
            assert client.post('/search',json=request).status_code==422
        response=client.post('/search',json={'query':'收益','company':'稀有公司','top_k':1,'n_cand':16})
        assert response.status_code==200
        assert response.json()['primary_count']==1
        assert response.json()['results'][1]['attached']
