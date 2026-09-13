"""Small real-model check; not a corpus retrieval evaluation."""
import json
from pathlib import Path
import time
import numpy as np
from finmhqa.retrieval.models import LocalModels


def main():
    start=time.monotonic()
    models=LocalModels('data/models/retrieval',batch_size=2,max_length=1024)
    loaded=time.monotonic()
    query='美的集团2025年上半年营业收入是多少？'
    texts=['美的集团2025年上半年营业收入为251,123,714千元。','今天讨论天气预报和周末出游安排。']
    vectors=models.encode([query,*texts]);encoded=time.monotonic()
    scores=models.score(query,texts);finished=time.monotonic()
    assert vectors.shape==(3,1024) and np.isfinite(vectors).all()
    assert np.allclose(np.linalg.norm(vectors,axis=1),1,atol=1e-5)
    assert len(scores)==2 and all(0<=s<=1 for s in scores)
    report=dict(device='cpu',dtype='float32',model_bundle_sha256=models.bundle_hash,shape=list(vectors.shape),norms=np.linalg.norm(vectors,axis=1).tolist(),dense_similarities=(vectors[1:]@vectors[0]).tolist(),rerank_scores=scores,relevant_above_irrelevant=scores[0]>scores[1],seconds=dict(load=loaded-start,embed=encoded-loaded,rerank=finished-encoded),retrieval_acceptance=False)
    target=Path('data/qa/retrieval/model_probe.json');target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
