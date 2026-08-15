"""FAISS 后端逻辑测试（mock faiss，验证 add/query/delete/持久化/多collection/门面）"""
import os, sys, tempfile, shutil
from types import SimpleNamespace

TMP = tempfile.mkdtemp(prefix="ragtest_")
PKG = os.path.join(TMP, "ragtest", "src")
os.makedirs(os.path.join(PKG, "core"), exist_ok=True)
open(os.path.join(PKG, "__init__.py"), "w", encoding="utf-8").close()
open(os.path.join(PKG, "core", "__init__.py"), "w", encoding="utf-8").close()

orig_cfg = open(r"H:\rag-knowledge-qa\src\config.py", encoding="utf-8").read().lstrip("\ufeff")
cfg_add = open(os.path.join(os.path.dirname(__file__), "config_append.py"), encoding="utf-8").read().lstrip("\ufeff")
open(os.path.join(PKG, "config.py"), "w", encoding="utf-8").write(orig_cfg + "\n" + cfg_add)

impl = os.path.dirname(__file__)
for f in ["vector_store.py", "faiss_backend.py", "milvus_backend.py"]:
    shutil.copy(os.path.join(impl, f), os.path.join(PKG, "core", f))

IDX = os.path.join(TMP, "faiss_index")
os.environ["FAISS_INDEX_DIR"] = IDX
os.environ["VECTOR_STORE_BACKEND"] = "faiss"
sys.path.insert(0, os.path.join(TMP, "ragtest"))

# ---- mock faiss ----
import numpy as np

class _IndexFlat:
    def __init__(self, dim):
        self.dim, self.ids, self.ntotal = dim, [], 0
        self.vecs = np.zeros((0, dim), dtype=np.float32)
    def add_with_ids(self, vecs, ids):
        self.ids.extend(int(i) for i in ids)
        self.vecs = np.vstack([self.vecs, vecs]) if self.ntotal else np.array(vecs, dtype=np.float32)
        self.ntotal = len(self.ids)
    def search(self, q, n):
        sims = self.vecs @ q[0]
        order = np.argsort(-sims)[:n]
        return sims[order].reshape(1, -1), np.array([self.ids[i] for i in order]).reshape(1, -1)
    def remove_ids(self, ids):
        idset = set(int(i) for i in ids)
        keep = [i for i in range(self.ntotal) if self.ids[i] not in idset]
        self.ids = [self.ids[i] for i in keep]
        self.vecs = self.vecs[keep]
        self.ntotal = len(self.ids)

class _IDMap2:
    def __init__(self, base): self.base = base
    @property
    def ntotal(self): return self.base.ntotal
    def add_with_ids(self, vecs, ids): self.base.add_with_ids(vecs, ids)
    def search(self, q, n): return self.base.search(q, n)
    def remove_ids(self, ids): self.base.remove_ids(ids)
    def reconstruct(self, i): return self.base.vecs[i]

class _HNSWSQ(_IndexFlat):
    def __init__(self, dim, m, qtype):
        super().__init__(dim)
        self.hnsw = SimpleNamespace(efConstruction=0, efSearch=0)
    def reconstruct(self, i): return self.vecs[i]

class _FakeFaiss:
    ScalarQuantizer = SimpleNamespace(QT_8bit="QT_8bit")
    _store = {}
    def normalize_L2(self, v):
        norms = np.linalg.norm(v, axis=1, keepdims=True)
        v[:] = v / np.maximum(norms, 1e-12)
    def IndexFlatIP(self, d): return _IndexFlat(d)
    def IndexIDMap2(self, base): return _IDMap2(base)
    def IndexHNSWSQ(self, d, m, qtype): return _HNSWSQ(d, m, qtype)
    def write_index(self, idx, path):
        self._store[path] = idx
        open(path, "wb").write(b"mock-index")
    def _unwrap(self, idx):
        while hasattr(idx, "base"):
            idx = idx.base
        return idx
    def read_index(self, path):
        src = self._unwrap(self._store[path])
        idx = _IndexFlat(src.dim)
        idx.ids = list(src.ids); idx.vecs = src.vecs.copy(); idx.ntotal = src.ntotal
        return idx

sys.modules["faiss"] = _FakeFaiss()
sys.modules.setdefault("dotenv", SimpleNamespace(load_dotenv=lambda *a, **k: None))

failures = []
def check(name, cond, detail=""):
    if cond: print("PASS", name)
    else: failures.append(name); print("FAIL", name, detail)

from src.core.vector_store import VectorStore, reset_backend

def fresh(idx_type):
    reset_backend()
    os.environ["FAISS_INDEX_TYPE"] = idx_type
    if os.path.isdir(IDX): shutil.rmtree(IDX)
    os.makedirs(IDX, exist_ok=True)
    return VectorStore()

# 1) flat 模式 CRUD
vs = fresh("flat")
vs.add(ids=["a1","a2","a3"], documents=["doc alpha","doc beta","doc gamma"],
       embeddings=[[1,0,0,0],[0,1,0,0],[0,0,1,0]],
       metadatas=[{"source_file":"x.md"},{"source_file":"x.md"},{"source_file":"y.md"}])
check("flat count=3", vs.count() == 3)
r = vs.query([1,0,0,0], n_results=3)
check("flat top1 a1", r["ids"][0][0] == "a1" and r["distances"][0][0] < 0.001, str(r))
check("flat 3 results sorted", len(r["ids"][0]) == 3 and r["distances"][0][1] >= r["distances"][0][0])
check("flat get_all=3", len(vs.get_all()["ids"]) == 3)
vs.delete(ids=["a2"])
check("flat delete count=2", vs.count() == 2 and "a2" not in vs.get_all()["ids"])

# 2) 持久化重载（不清目录，验证从磁盘加载）
reset_backend()
os.environ["FAISS_INDEX_TYPE"] = "flat"
vs2 = VectorStore()
check("reload count=2", vs2.count() == 2)
check("reload query top1 a1", vs2.query([1,0,0,0], n_results=2)["ids"][0][0] == "a1")
chunks = vs2.query_by_source("x.md")
check("query_by_source x.md -> a1", len(chunks) == 1 and chunks[0]["chunk_id"] == "a1", str(chunks))
check("query_by_source y.md -> a3", vs2.query_by_source("y.md")[0]["chunk_id"] == "a3")

# 3) sq8 模式（删除走重建）
vsq = fresh("hnsw-sq8")
vsq.add(ids=["s1","s2","s3"], documents=["sq a","sq b","sq c"], embeddings=[[1,0,0,0],[0,1,0,0],[0,0,1,0]])
check("sq8 count=3", vsq.count() == 3)
vsq.delete(ids=["s2"])
check("sq8 delete count=2", vsq.count() == 2)
rs = vsq.query([1,0,0,0], n_results=2)
check("sq8 query top1 s1", rs["ids"][0][0] == "s1", str(rs))
reset_backend()
os.environ["FAISS_INDEX_TYPE"] = "hnsw-sq8"
check("sq8 reload", VectorStore().count() == 2)

# 4) 多 collection
vsm = fresh("flat")
vsm.set_kb("kb1"); vsm.add(ids=["kb1a"], documents=["kb1 doc"], embeddings=[[1,0,0,0]])
vsm.set_kb("kb2"); vsm.add(ids=["kb2a"], documents=["kb2 doc"], embeddings=[[1,0,0,0]])
vsm.set_kb("kb1")
check("kb1 count=1", vsm.count() == 1)
cols = set(vsm._backend.list_collections())
check("collections kb1/kb2 (未写默认collection，无knowledge_base)", cols == {"kb1","kb2"}, str(cols))

print("\n=== %d failures ===" % len(failures))
sys.exit(1 if failures else 0)





