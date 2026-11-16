# Qdrant 전환 배포 절차 (EC2)

현재 배포(Vercel → Cloudflare Tunnel → EC2 FastAPI → TiDB·S3·Gemini)에 Qdrant를 EC2 안에 추가합니다. 임베딩은 Gemini 그대로이므로 다시 임베딩할 필요가 없고, 기존 `embedding_json`을 옮기기만 합니다.

```text
EC2
├─ custom-ta.service  (FastAPI, :8000)
└─ qdrant.service     (Qdrant, 127.0.0.1:6333, 외부 비공개)
```

**코드만 배포하면 동작은 바뀌지 않습니다.** 코드 기본값이 `VECTOR_STORE=tidb_json`이라, `.env`에 `VECTOR_STORE=qdrant`를 넣기 전까지는 기존 방식 그대로 동작합니다. 그래서 코드 배포와 전환을 나눠서 할 수 있습니다.

## 0. 확인

```bash
uname -m      # x86_64 또는 aarch64
free -h       # Qdrant는 이 규모에서 보통 수백 MB를 씁니다
```

메모리가 1GB 이하인 인스턴스라면 EC2 안에 띄우는 대신 Qdrant Cloud 무료 클러스터를 쓰고 `QDRANT_URL`과 `QDRANT_API_KEY`만 넣는 편이 안전합니다.

## 1. 코드 받기

```bash
cd /home/ubuntu/Back
git fetch && git switch <이 브랜치 또는 main>
.venv/bin/pip install -r requirements.txt
sudo systemctl restart custom-ta
curl http://127.0.0.1:8000/health/vector-store   # {"vector_store":"tidb_json",...}
```

여기까지는 기존 동작 그대로입니다.

## 2. Qdrant 설치 (공식 릴리스 바이너리, v1.20.1)

```bash
mkdir -p /home/ubuntu/qdrant && cd /home/ubuntu/qdrant
# x86_64:
curl -L -o qdrant.tar.gz https://github.com/qdrant/qdrant/releases/download/v1.20.1/qdrant-x86_64-unknown-linux-gnu.tar.gz
# aarch64(Graviton)이면 위 대신:
# curl -L -o qdrant.tar.gz https://github.com/qdrant/qdrant/releases/download/v1.20.1/qdrant-aarch64-unknown-linux-musl.tar.gz
tar -xzf qdrant.tar.gz && rm qdrant.tar.gz
./qdrant --version

sudo cp /home/ubuntu/Back/deploy/qdrant.service /etc/systemd/system/qdrant.service
sudo systemctl daemon-reload
sudo systemctl enable --now qdrant
curl http://127.0.0.1:6333/        # {"title":"qdrant - vector search engine",...}
```

보안 그룹에서 6333 포트를 열지 않습니다. 서비스 파일이 127.0.0.1에만 묶어 둡니다.

## 3. 설정

`/home/ubuntu/Back/.env`에 추가:

```env
VECTOR_STORE=qdrant
EMBEDDING_DIMENSION=768
QDRANT_URL=http://127.0.0.1:6333
QDRANT_COLLECTION=custom_ta_chunks
```

`EMBEDDING_DIMENSION`은 `GEMINI_EMBEDDING_OUTPUT_DIMENSIONALITY`와 같아야 합니다.

## 4. 기존 임베딩 옮기기

```bash
cd /home/ubuntu/Back
.venv/bin/python -m scripts.migrate_embeddings_to_qdrant --dry-run   # 옮길 개수와 건너뛸 개수 확인
.venv/bin/python -m scripts.migrate_embeddings_to_qdrant
```

여러 번 실행해도 중복되지 않습니다. 삭제된 문서의 청크는 옮기지 않습니다. 차원이 다르거나 읽을 수 없는 임베딩은 건너뛰고 개수를 알려 주며, 그 문서는 다시 업로드하면 됩니다.

## 5. 전환과 확인

```bash
sudo systemctl restart custom-ta
curl http://127.0.0.1:8000/health/vector-store   # {"vector_store":"qdrant","points":<옮긴 개수>}
journalctl -u custom-ta -f
```

브라우저에서 학생 계정으로 질문 하나, 교강사 계정으로 PDF 업로드 하나를 해 보고 `points`가 늘어나는지 확인합니다.

## 되돌리기

`.env`에서 `VECTOR_STORE=tidb_json`으로 바꾸고 `sudo systemctl restart custom-ta`.

`embedding_json`은 계속 저장되지 않으므로, **전환 뒤 새로 올린 문서는 되돌린 상태에서 검색되지 않습니다.** 그 문서는 다시 업로드해야 합니다.

## 배포 전 로컬 확인 (완료)

- `tests/test_rag_flow_e2e.py`: 회원가입 → 강의 개설 → PDF 2개 업로드·색인 → 학생 입장 → 질문 → 파일 삭제 → 다시 질문을 실제 API로 실행. `tidb_json`과 `qdrant`에서 응답, 출처, 검색된 청크가 모두 같고, 삭제 뒤 Qdrant에 남은 벡터 수가 맞는지 확인. 로컬 Qdrant 1.20.1 서버로도 통과.
- 설정값만으로 Qdrant 서버에 연결되는지와 이전 스크립트(미리보기, 실제 이전, 재실행, 읽을 수 없는 임베딩·차원 불일치·삭제 문서 건너뛰기)를 로컬 서버로 확인.
- 차이 하나: 질문과 점수가 정확히 같은 청크가 여럿이면(예: 겹치는 내용이 전혀 없어 모두 0점) 두 저장소가 고르는 순서가 다를 수 있습니다. 실제 임베딩에서는 점수가 정확히 같아지는 경우가 드뭅니다.

확인하지 못한 것: TiDB·S3·Gemini를 붙인 실제 서버에서의 동작(테스트는 SQLite, 임시 폴더, 단순 임베딩으로 대신함). 위 5단계가 그 확인입니다.
