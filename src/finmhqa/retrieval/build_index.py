"""Build an immutable FAISS/BM25 index after explicit corpus admission."""
import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import uuid
import numpy as np
import faiss
import bm25s
from .artifacts import admission, read_chunks, sha256
from .sparse import ChineseTokenizer


def build(chunks_path, out_dir, models, *, dictionary='configs/fin_dict.txt', qa_dir=None, reviewed_samples=None, experimental=False):
    out=Path(out_dir)
    if out.exists():raise FileExistsError(out)
    gate=admission(chunks_path,out,qa_dir,experimental=experimental,reviewed_samples=reviewed_samples)
    chunks=read_chunks(chunks_path)
    out.parent.mkdir(parents=True,exist_ok=True)
    temporary=out.with_name(out.name+'.building-'+uuid.uuid4().hex[:8])
    temporary.mkdir()
    shutil.copyfile(chunks_path,temporary/'chunk_meta.jsonl')
    if sha256(temporary/'chunk_meta.jsonl')!=gate['dataset_sha256']:raise ValueError('Dataset changed during build')
    shutil.copyfile(dictionary,temporary/'fin_dict.txt')
    tokenizer=ChineseTokenizer(temporary/'fin_dict.txt')
    sparse=bm25s.BM25(method='lucene',k1=1.5,b=.75)
    sparse.index([tokenizer(c['text']) for c in chunks],show_progress=False)
    sparse.save(temporary/'bm25')
    dense=faiss.IndexFlatIP(models.dimension)
    truncated=[]
    for start in range(0,len(chunks),64):
        batch=chunks[start:start+64]
        vectors=np.asarray(models.encode([c['text'] for c in batch]),dtype='float32')
        if vectors.shape!=(len(batch),models.dimension) or not np.isfinite(vectors).all() or (np.linalg.norm(vectors,axis=1)==0).any():
            raise ValueError('Embedding shape or numeric validity failure')
        faiss.normalize_L2(vectors);dense.add(vectors)
        truncated.extend(batch[i]['chunk_id'] for i in models.last_embed_truncated)
        print(f'EMBEDDED {start+len(batch)}/{len(chunks)}',flush=True)
    faiss.write_index(dense,str(temporary/'faiss.index'))
    manifest=dict(schema_version=1,admission=gate,count=len(chunks),dimension=models.dimension,
        model_bundle_sha256=models.bundle_hash,max_length=models.max_length,embedding_truncated_chunk_ids=truncated,
        tokenization='jieba-HMM-false-lower-alphanumeric',rrf_k=60,
        versions={n:version(n) for n in ('faiss-cpu','bm25s','jieba','numpy','torch','transformers')},
        files={p.relative_to(temporary).as_posix():sha256(p) for p in sorted(temporary.rglob('*')) if p.is_file()})
    (temporary/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    # mkdir reservation keeps a competing builder from replacing an index.
    out.mkdir()
    os.rename(temporary,out)
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chunks',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--models',default='data/models/retrieval')
    p.add_argument('--qa-dir',default='data/qa/docling_corpus')
    p.add_argument('--reviewed-samples')
    p.add_argument('--experimental',action='store_true')
    p.add_argument('--max-length',type=int,default=1024)
    p.add_argument('--batch-size',type=int,default=4)
    a=p.parse_args()
    admission(a.chunks,a.out,a.qa_dir,experimental=a.experimental,reviewed_samples=a.reviewed_samples)
    from .models import LocalModels
    models=LocalModels(a.models,max_length=a.max_length,batch_size=a.batch_size)
    result=build(a.chunks,a.out,models,qa_dir=a.qa_dir,reviewed_samples=a.reviewed_samples,experimental=a.experimental)
    print(json.dumps(dict(count=result['count'],admission=result['admission'],truncated=len(result['embedding_truncated_chunk_ids'])),ensure_ascii=False))

if __name__=='__main__':main()
