# 벡터 저장소: TiDB JSON 전수 비교에서 Qdrant로

## 처음 방식과 그 이유

초기 버전은 chunk 임베딩을 TiDB `document_chunks.embedding_json`에 JSON 문자열로 저장하고, 질문이 오면 해당 강의의 chunk를 전부 읽어 Python에서 코사인 유사도를 계산했습니다. 강의 하나의 자료가 PDF 몇 개 수준이라 전수 비교로 충분했고, 관리할 서비스를 늘리지 않으려는 선택이었습니다.

이 방식의 비용은 chunk 수에 비례합니다.

- 질문마다 강의의 모든 임베딩(768차원, JSON으로 chunk당 약 17KB)을 DB에서 애플리케이션으로 가져옵니다.
- 가져온 문자열을 `json.loads`로 풀고 순수 Python 반복문으로 유사도를 계산합니다.
- 상위 5개만 필요한데도 DB는 계산에 참여하지 않습니다.

## 전후 비교 실험

`scripts/benchmark_vector_search.py`로 기존 방식과 Qdrant를 같은 데이터에서 비교했습니다. 원본 수치는 `docs/experiments/`에 JSON으로 있습니다.

- 환경: Apple Silicon 노트북, Python 3.11, Qdrant 1.20.1 서버(localhost), 768차원, Top-5
- 전(before): 강의의 `embedding_json` 행을 모두 읽고, `json.loads`, 순수 Python 코사인, 정렬. TiDB 대신 로컬 SQLite에서 읽었으므로 네트워크 시간이 빠져 있고, 실제 서비스는 이보다 느립니다.
- 후(after): `QdrantVectorStore.search`
- 데이터: 합성 벡터 두 종류. `clustered`는 100개 주제로 뭉친 벡터, `random`은 서로 무관한 임의 벡터입니다. 실제 강의 자료 임베딩이 아닙니다.

### A. 강의 하나의 chunk 수가 늘 때 (clustered)

| chunk 수 | 전 p50 | 후 p50 | 후 p95 | recall@5 | 배율 |
|---:|---:|---:|---:|---:|---:|
| 200 | 23.6ms | 0.92ms | 1.09ms | 1.000 | 26배 |
| 2,000 | 237ms | 1.01ms | 1.12ms | 1.000 | 235배 |
| 20,000 | 2,382ms | 1.71ms | 1.92ms | 1.000 | 1,393배 |
| 50,000 | 6,093ms | 2.00ms | 2.41ms | 1.000 | 3,047배 |

기존 방식은 chunk 수에 정비례합니다(200개당 약 23ms). 시간의 97% 이상이 DB 읽기가 아니라 JSON 파싱과 Python 반복문에서 나옵니다. 2,000 chunk에서 237ms 중 읽기는 5ms입니다.

### B. Custom-TA의 실제 형태: 강의가 많고 강의마다 작을 때

전체 50,000 chunk가 200개 강의에 250개씩 나뉘어 있고, 그중 한 강의에서 검색합니다.

| | p50 | recall@5 |
|---|---:|---:|
| 전 | 29.6ms | 1.000 (전수 비교) |
| 후 | 0.94ms | 1.000 |

기존 방식의 비용은 전체 데이터가 아니라 **강의 하나의 chunk 수**로 정해집니다. 강의당 수백 chunk인 현재 규모에서는 30ms 안팎이라 사용자가 체감할 차이는 아닙니다. 답변 생성(LLM 호출)이 초 단위이기 때문입니다.

### C. 동시에 8명이 질문할 때 (강의당 2,000 chunk)

| | 8건이 모두 끝나기까지 |
|---|---:|
| 전 | 1,910ms |
| 후 | 4.8ms |

기존 계산은 async 핸들러 안에서 도는 CPU 작업이라 이벤트 루프를 막습니다. 한 학생의 검색이 도는 동안 다른 요청이 전부 기다리므로 8건이 사실상 순서대로 처리됩니다. 단일 요청 지연보다 이 점이 실제 서비스에 더 큰 문제입니다.

### D. 인덱스 정확도와 탐색 강도 (50,000 chunk)

HNSW는 근사 검색이라 전수 비교와 결과가 다를 수 있습니다. `hnsw_ef`(검색 때 살펴볼 후보 수)를 바꿔 가며 쟀습니다.

| 설정 | p50 | recall@5 (random) | recall@5 (clustered) |
|---|---:|---:|---:|
| 기본값 | 2.25ms | 0.478 | 1.000 |
| `hnsw_ef=128` | 2.47ms | 0.544 | 1.000 |
| `hnsw_ef=256` | 3.38ms | 0.764 | 1.000 |
| `hnsw_ef=512` | 4.56ms | 0.922 | 1.000 |
| `hnsw_ef=1024` | 5.65ms | 0.988 | 1.000 |
| 인덱스 없이 전수 비교 | 3.06ms | 1.000 | 1.000 |

(p50은 random 기준. clustered는 각 설정에서 0.3~1ms 더 빠릅니다.)

- 임의 벡터에서는 기본 설정의 recall이 0.48까지 떨어집니다. 모든 벡터가 서로 비슷하게 멀어서 근사 인덱스에 가장 불리한 경우입니다. 주제별로 뭉친 벡터에서는 1.000입니다. 실제 임베딩은 이 둘 사이 어딘가에 있고, 어디인지는 실제 자료로 재야 압니다.
- `QDRANT_HNSW_EF`로 탐색 강도를 올리면 몇 ms를 더 쓰고 recall을 되찾을 수 있습니다.
- Qdrant는 인덱스가 만들어지기 전(이 실험에서는 2,000 chunk 이하)과 필터에 걸리는 점이 적을 때(실험 B)는 전수 비교를 합니다. 그래서 그 구간의 recall은 항상 1.000입니다.

### 저장 크기

| | chunk당 |
|---|---:|
| JSON 문자열 | 약 17.0KB |
| float32 벡터 | 3.1KB |

### 해석

- 이 규모에서 속도 차이의 대부분은 HNSW 인덱스가 아니라, 벡터 계산을 JSON과 Python 반복문에서 네이티브 연산으로 옮긴 데서 나옵니다. 50,000 chunk에서 Qdrant는 인덱스를 끄고 전수 비교를 해도 3ms입니다.
- 인덱스의 이점은 강의 하나가 수십만 chunk 이상이 될 때 커지며, 그 대가로 recall을 관리해야 합니다.
- 현재 Custom-TA 규모(강의당 수백 chunk)에서 전환의 실질적 이득은 지연 30ms 단축보다, 검색이 이벤트 루프를 막지 않게 된 것과 강의 자료가 늘어도 지연이 유지된다는 것입니다.

재현:

```bash
python -m scripts.benchmark_vector_search --qdrant-url http://localhost:6333 --data clustered --out docs/experiments/vector_search_clustered.json
python -m scripts.benchmark_vector_search --qdrant-url http://localhost:6333 --data random --out docs/experiments/vector_search_random.json
```

## 실제 강의자료 통제 실험: 소프트웨어공학론

실제 강의자료로, **벡터 저장소만 바꾸고 나머지는 모두 고정**한 실험입니다. 원본 수치는 `docs/experiments/real_lecture_software_engineering.json`에 있습니다.

| 고정한 조건 (양쪽 동일) | 값 |
|---|---|
| 강의자료 | 전남대 소프트웨어공학론 PDF 11개(482쪽)를 강의 하나에 올림 |
| 청크 | 앱의 분할 코드로 만든 483개 (1,200자, 겹침 200자) |
| 임베딩 | Upstage `solar-embedding-2-passage`(청크) / `solar-embedding-2-query`(질문), 1,024차원. 한 번 계산해 양쪽이 같은 벡터를 씀 |
| 질문 | 학생 질문 32개, 같은 질문 벡터 |
| 저장 본문 | 양쪽 모두 청크 전체 본문 |
| 검색 | 코사인 유사도, Top-5 |
| 측정 | 질문마다 양쪽 번갈아 50회, 첫 호출 예열 후 중앙값 |

| 실험 변수 | 전 | 후 |
|---|---|---|
| 벡터 저장·검색 | DB 컬럼의 JSON 문자열 + Python 코사인 전수 비교 | Qdrant 1.20.1 서버 (localhost) |

정답 기준: 질문마다 그 주제를 다루는 강의를 과목 정리 노트를 보고 미리 정했습니다(검색 결과를 보고 정하지 않음).

### 결과

| 항목 | 전 | 후 |
|---|---:|---:|
| 질문당 검색 시간 (32개 질문의 중앙값) | 78.49ms | 1.64ms |
| 가장 느린 질문 | 79.36ms | 1.71ms |
| 상위 5개 일치 (순서까지) | 기준 | 32/32 |
| 정답 강의가 1위 | 29/32 (91%) | 29/32 (91%) |
| 정답 강의가 5위 안 | 31/32 (97%) | 31/32 (97%) |
| MRR@5 | 0.938 | 0.938 |

### 해석

- **벡터 저장소만 바꾼 효과는 속도뿐입니다.** 검색 시간이 47.9배 줄었고, 결과는 32개 질문 모두 순서까지 같았습니다. 청크 483개는 Qdrant가 인덱스 없이 전수 비교하는 규모라 근사 오차가 없습니다.
- **검색 품질은 저장소가 아니라 임베딩이 결정합니다.** 같은 벡터와 같은 코사인 계산이므로 적중률이 같을 수밖에 없고, 실험으로도 그렇게 나왔습니다.
- **놓친 질문은 언어 차이 때문입니다.** "소프트웨어 위기란 무엇이야?"의 답은 영어 슬라이드인 1-2 강의 14~16쪽("The Software Crisis")에만 있는데 상위 5개에 들지 못했습니다. 1위를 놓친 "소프트웨어공학의 정의"도 영어 강의(1-1, 1-2) 질문입니다. 저장소로는 해결되지 않으며, 질문 번역이나 키워드 검색 병행(하이브리드)이 다음 개선 후보입니다.
- **원래 코드의 1,000자 잘림은 따로 다뤘습니다.** 기존 코드는 본문을 앞 1,000자만 저장했지만, 이 실험에서는 변수를 하나로 두려고 양쪽에 전체 본문을 넣었습니다. 이 과목은 슬라이드라 청크 중앙값이 167자이고 1,000자를 넘는 청크가 1개뿐이어서, 잘림을 켜도(`--before-text preview`) 질문당 본문 차이는 5자 수준이었습니다.

### 한계

- 전 방식은 TiDB 대신 로컬 SQLite에서 읽었습니다. 실제 서비스에서는 모든 임베딩을 네트워크로 받아와야 하므로 전 방식이 더 느립니다.
- 서비스는 Gemini(768차원)를 쓰므로, 이 실험의 적중률을 서비스에 그대로 옮길 수는 없습니다. 전 방식의 계산량은 차원에 비례하므로 768차원에서는 전 방식이 조금 덜 느릴 것으로 봅니다(추정).
- 질문 32개와 정답 강의는 한 사람이 만들었습니다.

재현 (`.env`에 `UPSTAGE_API_KEY` 필요):

```bash
python -m scripts.experiment_real_lecture --pdf-dir "<강의 PDF 폴더>" --embedder upstage --before-text full --repeats 50 --qdrant-url http://localhost:6333 --out docs/experiments/real_lecture_software_engineering.json
```

## 왜 Qdrant인가

- 강의별 검색이 핵심인데, Qdrant는 `course_id` payload 인덱스를 벡터 검색 안에서 함께 적용합니다. 검색 후 걸러내는 방식과 달리 필터 때문에 Top-K가 모자라지 않습니다.
- 오픈소스라 로컬, Docker, 관리형 클라우드에서 같은 코드로 동작합니다. 테스트는 메모리 모드로 돌립니다.
- 비동기 Python 클라이언트가 있어 FastAPI의 async 흐름을 막지 않습니다.

Pinecone은 클라우드 전용이라 계정 없이 테스트를 돌릴 수 없고, FAISS는 저장·삭제·필터를 직접 구현해야 하는 라이브러리여서 제외했습니다. TiDB 자체 벡터 타입도 후보였으나, 저장소 계층을 분리해 교체 가능하게 만드는 쪽을 택했습니다.

## 구조

`src/vectorstore/`에 저장소 계층을 분리했습니다.

| 파일 | 역할 |
|---|---|
| `base.py` | `VectorStore` 인터페이스 (`upsert`, `search`, `delete_document`) |
| `qdrant_store.py` | Qdrant 구현. point id는 `document_chunk_id`, payload에 chunk 본문과 출처 정보 |
| `tidb_json_store.py` | 기존 전수 비교 구현. 대체 경로이자 측정 기준선 |

`VECTOR_STORE` 환경변수로 구현을 고릅니다. 코드 기본값은 기존 배포가 깨지지 않도록 `tidb_json`이고, `.env.example`은 `qdrant`입니다.

함께 바뀐 동작:

- Qdrant payload에는 chunk 본문 전체가 들어갑니다. 기존에는 DB에 앞 1,000자만 남겨 답변 생성에도 잘린 본문이 쓰였습니다.
- 파일을 삭제하면 해당 문서의 벡터를 먼저 지우고 나서 삭제 표시를 합니다. 벡터 삭제가 실패하면 파일이 목록에 남아 다시 시도할 수 있습니다.
- 문서 색인이 중간에 실패하면 이미 올라간 벡터를 지웁니다.

## 전환 절차

1. Qdrant를 준비합니다. Docker(`docker run -p 6333:6333 qdrant/qdrant`) 또는 Qdrant Cloud.
2. `.env`에 `VECTOR_STORE=qdrant`, `QDRANT_URL`, 필요하면 `QDRANT_API_KEY`를 넣습니다. `QDRANT_URL`을 비우면 로컬 디스크 내장 모드로 동작하며 개발용입니다.
3. 기존 임베딩을 옮깁니다. 여러 번 실행해도 중복되지 않습니다.

```bash
python -m scripts.migrate_embeddings_to_qdrant --dry-run
python -m scripts.migrate_embeddings_to_qdrant
```

4. 서버를 재시작합니다.

`embedding_json`은 지우지 않으므로 `VECTOR_STORE=tidb_json`으로 되돌릴 수 있습니다. 단, 전환 후 새로 올린 문서는 Qdrant에만 들어가므로 되돌리면 그 문서는 다시 업로드해야 합니다.

## 남은 한계

- TiDB와 Qdrant는 한 트랜잭션으로 묶이지 않습니다. 색인 중 Qdrant 쓰기는 성공하고 DB 커밋이 실패하면 벡터를 지우려고 시도하지만, 그 삭제까지 실패하면 주인 없는 벡터가 남습니다.
- 이전한 기존 chunk의 payload 본문은 앞 1,000자뿐입니다. 전체 본문을 쓰려면 문서를 다시 업로드해야 합니다.
- 실험은 합성 벡터로 했습니다. 실제 강의 자료와 질문으로 검색 품질(정답 chunk가 Top-5에 드는 비율)과 실제 임베딩에서의 HNSW recall을 평가한 적은 없습니다.
- 실험의 기존 방식 수치는 로컬 SQLite 기준이라 TiDB 네트워크 왕복이 빠져 있습니다.

## 테스트

```bash
pip install -r requirements-dev.txt
pytest
```
