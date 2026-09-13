"""One pinned Chinese tokenization path for both indexing and queries."""
import re
import jieba


class ChineseTokenizer:
    def __init__(self, dictionary):
        self.tokenizer=jieba.Tokenizer()
        self.tokenizer.load_userdict(str(dictionary))

    def __call__(self,text):
        return [w.lower() for w in self.tokenizer.cut(text,HMM=False) if re.search(r'\w',w)]
