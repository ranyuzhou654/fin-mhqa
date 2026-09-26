"""Retrieval trace: what each recall channel found, how RRF fused them, and how the reranker ordered the result.

Pure bookkeeping over lists the retriever already computes; it never changes a ranking. Returned only when a
caller asks for it (search(..., trace=True)), so agent tool responses stay exactly as before.
"""


def label(chunk):
    title = chunk.get('table_title') or (chunk.get('section_path') or '').split('>')[-1].strip()
    return {'chunk_id': chunk['chunk_id'], 'company': chunk.get('company'), 'chunk_type': chunk.get('chunk_type'),
            'page_start': chunk.get('page_start'), 'title': title[:60]}


def build_trace(chunks, mode, n_cand, top_k, dense=None, sparse=None, fused=None, reranked=None, attached=()):
    """dense / sparse / fused: lists of (index, score) in rank order; reranked: all candidates (index, score) sorted."""
    def ranked(pairs, key):
        return [{**label(chunks[i]), 'rank': r, key: round(float(s), 6)} for r, (i, s) in enumerate(pairs or [], 1)]
    dense_rank = {i: r for r, (i, _) in enumerate(dense or [], 1)}
    sparse_rank = {i: r for r, (i, _) in enumerate(sparse or [], 1)}
    candidates = fused if mode == 'hybrid' else dense if mode == 'dense' else sparse
    candidate_rank = {i: r for r, (i, _) in enumerate(candidates or [], 1)}
    final = [i for i, _ in (reranked or [])[:top_k]]

    def source(i):
        both = i in dense_rank and i in sparse_rank
        return 'both' if both else 'dense' if i in dense_rank else 'bm25' if i in sparse_rank else None

    trace = {'mode': mode, 'n_cand': n_cand, 'top_k': top_k,
             'dense': ranked(dense, 'score') if mode in ('dense', 'hybrid') else None,
             'bm25': ranked(sparse, 'score') if mode in ('bm25', 'hybrid') else None,
             'fused': [{**label(chunks[i]), 'rank': r, 'rrf_score': round(float(s), 6), 'dense_rank': dense_rank.get(i),
                        'bm25_rank': sparse_rank.get(i)} for r, (i, s) in enumerate(fused or [], 1)] if mode == 'hybrid' else None,
             'reranked': [{**label(chunks[i]), 'rank': r, 'rerank_score': round(float(s), 6), 'candidate_rank': candidate_rank.get(i),
                           'dense_rank': dense_rank.get(i), 'bm25_rank': sparse_rank.get(i), 'source': source(i), 'selected': r <= top_k}
                          for r, (i, s) in enumerate(reranked or [], 1)],
             'attached_parents': [label(chunks[j]) for j in attached]}
    trace['summary'] = {
        'dense_hits': len(dense or []), 'bm25_hits': len(sparse or []),
        'overlap': len(set(dense_rank) & set(sparse_rank)),
        'final_from': {k: sum(source(i) == k for i in final) for k in ('both', 'dense', 'bm25')} if mode == 'hybrid' else None,
        'final_promoted_by_reranker': sum(candidate_rank.get(i, 0) > top_k for i in final)}
    return trace
