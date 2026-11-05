
import os
import re
import math
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")

def load_docs():
    docs = []
    for name in sorted(os.listdir(DATA_DIR)):
        if name.endswith(".md"):
            with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
                docs.append((name, f.read()))
    return docs

def split_chunks(text, size, overlap):
    text = re.sub(r"\n{2,}", "\n", text.strip())
    chunks, start = [], 0
    while start < len(text):
        part = text[start:start + size].strip()
        if part:
            chunks.append(part)
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


def build_chunks(docs, size, overlap):
    result = []
    for name, text in docs:
        for c in split_chunks(text, size, overlap):
            result.append((name, c))
    return result

def tokenize(s):
    s = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s)
    toks = [s[i:i + 2] for i in range(len(s) - 1)]
    return toks if toks else list(s)

class BM25:
    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(c)) for _, c in chunks]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.N = len(chunks)
        self.avgdl = sum(self.lens) / max(self.N, 1)

        df = Counter()
        for tf in self.tfs:
            for w in tf:
                df[w] += 1
        self.idf = {
            w: math.log(1 + (self.N - n + 0.5) / (n + 0.5))
            for w, n in df.items()
        }

    def search(self, query, topk=3):
        scores = []
        for i, tf in enumerate(self.tfs):
            score = 0.0
            for w in tokenize(query):
                if w not in tf:
                    continue
                score += self.idf[w] * (tf[w] * (self.k1 + 1)) / (
                    tf[w] + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avgdl)
                )
            scores.append((score, i))
        scores.sort(key=lambda x: x[0], reverse=True)
        return [(self.chunks[i][0], self.chunks[i][1], round(s, 2))
                for s, i in scores[:topk] if s > 0]

QUESTIONS = [
    ("学生最多可以借几册图书", "library_rules.md", "8 册"),
    ("普通图书的借期是多少天", "library_rules.md", "30 天"),
    ("续借一次可以延长多少天", "library_rules.md", "15 天"),
    ("逾期罚金按什么标准计算", "library_rules.md", "每册每天 0.1 元"),
    ("单册罚金累计上限是多少", "library_rules.md", "20 元"),
    ("预约到书后为读者保留几天", "library_rules.md", "3 天"),
    ("图书遗失了要怎么赔偿", "library_rules.md", "原价的2 倍"),
    ("书目表和副本表有什么区别", "library_rules.md", "独立条码"),
    ("快递服务中心几点关门", "express_rules.md", "21:00"),
    ("取件需要出示什么凭证", "express_rules.md", "取件码"),
    ("取件码连续输错几次会被锁定", "express_rules.md", "5 次"),
    ("包裹入库后免费存放多久", "express_rules.md", "72 小时"),
    ("超过 30 天无人认领的包裹怎么处理", "express_rules.md", "退回寄件方"),
    ("每人每天最多代取几件快递", "express_rules.md", "3 件"),
    ("大件包裹在什么时间办理", "express_rules.md", "9:00 至 18:00"),
    ("忘记登录密码怎么找回", "campus_faq.md", "重置链接"),
    ("密码连续输错几次账号会被锁定", "campus_faq.md", "5 次"),
    ("校园服务系统每天几点开放", "campus_faq.md", "6:00 至 23:00"),
    ("刚归还的图书状态为什么没更新", "campus_faq.md", "5 分钟"),
    ("导出的借阅记录是什么格式", "campus_faq.md", "CSV"),

]

def norm(s):
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s)


def evaluate(chunks, topk=3):

    bm = BM25(chunks)
    doc_hit, ans_hit = 0, 0
    for q, gold_doc, key in QUESTIONS:
        results = bm.search(q, topk)
        if any(doc == gold_doc for doc, _, _ in results):
            doc_hit += 1
        if any(norm(key) in norm(text) for _, text, _ in results):
            ans_hit += 1
    return doc_hit, ans_hit, len(QUESTIONS), bm


def coverage(query, text):
    q, t = set(tokenize(query)), set(tokenize(text))
    return len(q & t) / max(len(q), 1)


def make_prompt(question, results):
    context = "\n".join(
        "[{}] {}".format(i + 1, text) for i, (_, text, _) in enumerate(results)
    )
    return (
        "请只根据下面的资料回答问题。资料里没有就回答「资料中没有相关信息」，不要编造。\n"
        "回答时标注引用了第几段资料。\n\n"
        "资料：\n" + context + "\n\n问题：" + question + "\n回答："
    )

def main():
    docs = load_docs()

    total_chars = sum(len(t) for _, t in docs)
    for name, text in docs:
        print("  {:22s} {:5d} 字".format(name, len(text)))
    print("  共 {} 篇文档，{} 字\n".format(len(docs), total_chars))

    plans = [
        ("方案A", (35, 0),  "切太碎，答案常被切断"),
        ("方案B", (80, 0),  "大小合适，但没有重叠"),
        ("方案C", (80, 20), "大小合适 + 20 字重叠"),

    ]
    built = []
    for label, (size, ov), note in plans:
        ch = build_chunks(docs, size, ov)
        built.append((label, (size, ov), note, ch))
        avg = sum(len(c) for _, c in ch) / max(len(ch), 1)
        print(" {} size={:3d} overlap={:2d} → 切出 {:3d} 块，平均 {:3.0f} 字/块  ({})".format(
            label, size, ov, len(ch), avg, note))


    res = {}
    for label, _, _, ch in built:
        dh, ah, total, bm = evaluate(ch)
        res[label] = bm
        print("  {}  {:2d}/{:2d} = {:3.0f}%    {:2d}/{:2d} = {:3.0f}%"
        .format(label, dh, total, dh / total * 100, ah, total, ah / total * 100))

    bm = res["方案C"]
    results = bm.search("逾期", 8)
    for i, (doc, text, score) in enumerate(results, 1):
        print("  Top{} [{:.2f}] {} : {}".format(i, score, doc, text[:60].replace("\n", " ")))



    print(make_prompt("逾期罚金按什么标准计算" + "\n ",results))


if __name__ == "__main__":
    main()
