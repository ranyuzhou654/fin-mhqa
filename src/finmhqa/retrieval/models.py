"""Author-supported Transformers inference, CPU fp32, local files only."""
import os
from pathlib import Path

os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'


class LocalModels:
    def __init__(self, root, *, batch_size=4, max_length=1024, threads=4):
        import torch
        from transformers import AutoTokenizer, AutoModel, AutoModelForSequenceClassification
        from .artifacts import verify_model_bundle
        if batch_size<1 or not 32<=max_length<=8192 or not 1<=threads<=16:
            raise ValueError('Invalid inference limits')
        self.bundle_hash=verify_model_bundle(root)
        self.batch_size=batch_size;self.max_length=max_length
        torch.set_num_threads(threads)
        root=Path(root)
        kwargs=dict(local_files_only=True,trust_remote_code=False)
        self.embed_tokenizer=AutoTokenizer.from_pretrained(root/'bge-m3',**kwargs)
        self.rerank_tokenizer=AutoTokenizer.from_pretrained(root/'bge-reranker-v2-m3',**kwargs)
        self.embed_model=AutoModel.from_pretrained(root/'bge-m3',**kwargs).float().to('cpu').eval()
        self.rerank_model=AutoModelForSequenceClassification.from_pretrained(root/'bge-reranker-v2-m3',**kwargs).float().to('cpu').eval()
        self.dimension=self.embed_model.config.hidden_size
        self.last_embed_truncated=[];self.last_rerank_truncated=[]

    def encode(self, texts):
        import numpy as np
        import torch
        self.last_embed_truncated=[]
        result=[]
        with torch.inference_mode():
            for start in range(0,len(texts),self.batch_size):
                batch=texts[start:start+self.batch_size]
                lengths=[len(v) for v in self.embed_tokenizer(batch,add_special_tokens=True,truncation=False)['input_ids']]
                self.last_embed_truncated.extend(start+i for i,n in enumerate(lengths) if n>self.max_length)
                inputs=self.embed_tokenizer(batch,padding=True,truncation=True,max_length=self.max_length,return_tensors='pt')
                vectors=self.embed_model(**inputs).last_hidden_state[:,0]
                vectors=torch.nn.functional.normalize(vectors,p=2,dim=1)
                result.append(vectors.cpu().numpy().astype('float32'))
        return np.concatenate(result) if result else np.empty((0,self.dimension),dtype='float32')

    def score(self, query, texts):
        import torch
        self.last_rerank_truncated=[]
        query_length=len(self.rerank_tokenizer.encode(query,add_special_tokens=False))
        if query_length>min(256,self.max_length//2):raise ValueError('Query exceeds reranker query-token limit')
        scores=[]
        with torch.inference_mode():
            for start in range(0,len(texts),self.batch_size):
                batch=texts[start:start+self.batch_size]
                raw=self.rerank_tokenizer([query]*len(batch),batch,truncation=False)['input_ids']
                self.last_rerank_truncated.extend(start+i for i,ids in enumerate(raw) if len(ids)>self.max_length)
                inputs=self.rerank_tokenizer([query]*len(batch),batch,padding=True,truncation='only_second',max_length=self.max_length,return_tensors='pt')
                values=torch.sigmoid(self.rerank_model(**inputs).logits.view(-1).float())
                scores.extend(values.cpu().tolist())
        return scores
