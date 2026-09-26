import json
from importlib.metadata import version
from pathlib import Path
import threading
import numpy as np
import faiss
import bm25s
from .artifacts import read_chunks, sha256
from .sparse import ChineseTokenizer
from .trace import build_trace


def parent_key(chunk):
    return chunk['company'],chunk['source_sha256'],chunk.get('parent_table_id'),chunk.get('part')


class HybridRetriever:
    def __init__(self,index_dir,models,*,allow_experimental=False):
        root=Path(index_dir)
        self.manifest=json.loads((root/'manifest.json').read_text())
        if self.manifest.get('schema_version')!=1:raise ValueError('Unsupported index schema')
        status=self.manifest['admission']['status']
        if status not in ('ACCEPTED','EXPERIMENTAL_UNREVIEWED'):raise ValueError('Unknown index admission')
        if status!='ACCEPTED' and not allow_experimental:raise ValueError('Experimental index requires explicit opt-in')
        for relative,digest in self.manifest['files'].items():
            path=root/relative
            if not path.resolve().is_relative_to(root.resolve()) or sha256(path)!=digest:
                raise ValueError(f'Index artifact integrity failure: {relative}')
        required={'chunk_meta.jsonl','faiss.index','fin_dict.txt',
                  'bm25/data.csc.index.npy','bm25/indices.csc.index.npy','bm25/indptr.csc.index.npy','bm25/vocab.index.json','bm25/params.index.json'}
        if not required.issubset(self.manifest['files']):raise ValueError('Incomplete index manifest')
        for library in ('bm25s','jieba','transformers','torch','faiss-cpu','numpy'):
            if self.manifest['versions'].get(library)!=version(library):
                raise ValueError(f'Index runtime version differs: {library}')
        if sha256(root/'chunk_meta.jsonl')!=self.manifest['admission']['dataset_sha256']:
            raise ValueError('Index evidence differs from admitted dataset')
        if self.manifest['model_bundle_sha256']!=models.bundle_hash or self.manifest['max_length']!=models.max_length:
            raise ValueError('Index and inference model configuration differ')
        self.models=models;self.chunks=read_chunks(root/'chunk_meta.jsonl')
        self.dense_index=faiss.read_index(str(root/'faiss.index'))
        self.sparse_index=bm25s.BM25.load(root/'bm25',allow_pickle=False)
        self.tokenizer=ChineseTokenizer(root/'fin_dict.txt')
        if self.dense_index.ntotal!=len(self.chunks) or len(self.chunks)!=self.manifest['count'] or self.dense_index.d!=models.dimension:
            raise ValueError('Index count or dimension mismatch')
        if self.sparse_index.scores['num_docs']!=len(self.chunks):raise ValueError('Sparse corpus count differs')
        self.truncated=set(self.manifest['embedding_truncated_chunk_ids'])
        self.parents={}
        for i,c in enumerate(self.chunks):
            if c['chunk_type']=='table_md':self.parents.setdefault(parent_key(c),[]).append(i)
        self.lock=threading.Lock()

    @staticmethod
    def rrf(lists,k=60):
        if k<1:raise ValueError('RRF k must be positive')
        scores={}
        for ranked in lists:
            seen=set()
            for rank,(i,_) in enumerate(ranked,1):
                if i not in seen:scores[i]=scores.get(i,0)+1/(k+rank)
                seen.add(i)
        return sorted(scores.items(),key=lambda item:(-item[1],item[0]))

    def _eligible(self,company):
        return {i for i,c in enumerate(self.chunks) if company is None or company in (c['company'],c.get('company_code'))}

    def dense(self,query,k=30,company=None):
        eligible=self._eligible(company)
        if not eligible:return []
        vector=np.asarray(self.models.encode([query]),dtype='float32')
        if vector.shape!=(1,self.models.dimension) or not np.isfinite(vector).all() or np.linalg.norm(vector)==0:
            raise ValueError('Invalid query embedding')
        faiss.normalize_L2(vector)
        # All candidates are scored before filtering, so a rare company cannot
        # be starved by a fixed global overfetch factor.
        scores,ids=self.dense_index.search(vector,len(self.chunks))
        return sorted(((int(i),float(s)) for i,s in zip(ids[0],scores[0]) if i in eligible),key=lambda x:(-x[1],x[0]))[:k]

    def sparse(self,query,k=30,company=None):
        eligible=self._eligible(company)
        if not eligible:return []
        tokens=self.tokenizer(query)
        if not tokens or not any(t in self.sparse_index.vocab_dict for t in tokens):return []
        scores=self.sparse_index.get_scores(tokens)
        if not np.isfinite(scores).all():raise ValueError('Nonfinite BM25 scores')
        return sorted(((i,float(scores[i])) for i in eligible if scores[i]>0),key=lambda x:(-x[1],x[0]))[:k]

    def _fmt(self,index,score,attached=False,reranker_truncated=False):
        c=self.chunks[index]
        return dict(c,section=c['section_path'],score=float(score),score_kind='inherited_from_kv' if attached else 'reranker_sigmoid',
                    attached=attached,embedding_truncated=c['chunk_id'] in self.truncated,
                    reranker_truncated=reranker_truncated,index_admission=self.manifest['admission']['status'])

    def search(self,query,mode='hybrid',top_k=5,company=None,expand_parent=True,n_cand=30,trace=False):
        """Results as before; with trace=True returns (results, trace) describing both recall channels, RRF and rerank."""
        if not isinstance(query,str) or not query.strip() or len(query)>2000:raise ValueError('Query must contain 1–2000 characters')
        if mode not in ('dense','bm25','hybrid'):raise ValueError('Unsupported retrieval mode')
        if type(top_k) is not int or type(n_cand) is not int or not 1<=top_k<=20 or not top_k<=n_cand<=100:
            raise ValueError('Require 1 <= top_k <= 20 and top_k <= n_cand <= 100')
        with self.lock:
            empty=([],build_trace(self.chunks,mode,n_cand,top_k)) if trace else []
            if not self._eligible(company):return empty
            dense=sparse=fused=None
            if mode=='dense':candidates=dense=self.dense(query,n_cand,company)
            elif mode=='bm25':candidates=sparse=self.sparse(query,n_cand,company)
            else:
                dense,sparse=self.dense(query,n_cand,company),self.sparse(query,n_cand,company)
                candidates=fused=self.rrf([dense,sparse])[:n_cand]
            if not candidates:return empty
            ids=[i for i,_ in candidates]
            scores=self.models.score(query,[self.chunks[i]['text'] for i in ids])
            if len(scores)!=len(ids) or not np.isfinite(scores).all():raise ValueError('Invalid reranker output')
            truncated={ids[j] for j in self.models.last_rerank_truncated}
            ordered=sorted(zip(ids,scores),key=lambda x:(-x[1],x[0]))
            ranked=ordered[:top_k]
            results=[self._fmt(i,s,reranker_truncated=i in truncated) for i,s in ranked]
            have={r['chunk_id'] for r in results};attached=[]
            if expand_parent:
                for i,score in ranked:
                    c=self.chunks[i]
                    if c['chunk_type']=='table_kv':
                        for j in self.parents.get(parent_key(c),[]):
                            if self.chunks[j]['chunk_id'] not in have:
                                results.append(self._fmt(j,score,attached=True));have.add(self.chunks[j]['chunk_id']);attached.append(j)
            if trace:return results,build_trace(self.chunks,mode,n_cand,top_k,dense,sparse,fused,ordered,attached)
            return results
