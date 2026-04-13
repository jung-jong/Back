# Custom-TA 백엔드

Custom-TA는 강의 자료를 기반으로 학생 질문에 답변하고, 학습 데이터를 분석해 교강사에게 개입 제안을 제공하는 RAG 기반 AI 조교 백엔드입니다. FastAPI, SQLAlchemy, TiDB, AWS S3, Gemini API를 사용해 구현했습니다.

## 핵심 목표

- 교강사가 업로드한 PDF 강의 자료를 분석해 학생 질문에 근거 있는 답변 제공
- 답변에 사용된 강의 자료와 페이지 출처를 함께 저장
- 학생의 질문 키워드, 퀘스트 오답, 취약 개념을 누적 분석
- 교강사 대시보드에 보충 퀘스트, 동기부여 메시지, 추가 자료 업로드 제안 제공
- 추후 모델과 저장소를 교체하기 쉽도록 AI, Storage, DB 계층을 분리

## AI 협업 산출물

본 프로젝트는 공모전 심사 기준에 맞춰 AI와의 기획, 설계, 구현 협업 과정을 문서로 남겼습니다.

- `docs/AI_COLLABORATION_GUIDE.md`: AI 개발 에이전트 협업 지침서
- `docs/PRODUCT_REQUIREMENTS.md`: 제품 기획 및 기능 정의서
- `docs/AI_DEVELOPMENT_LOG.md`: AI 협업 개발 로그

## 기술 스택

| 영역 | 사용 기술 |
|---|---|
| Backend | FastAPI, Python 3.10+ |
| ORM | SQLAlchemy 2.0 Async |
| Database | TiDB, MySQL 호환 |
| LLM | Gemini 2.5 Flash-Lite |
| Embedding | Gemini Embedding 001 |
| File Storage | AWS S3, Presigned URL |
| RAG 저장 | TiDB `document_chunks.embedding_json` |
| Auth | JWT Bearer Token |

## RAG 동작 구조

현재 RAG는 Pinecone 없이 S3, TiDB, Gemini만으로 동작합니다.

```text
PDF 업로드
→ AWS S3에 원본 PDF 저장
→ S3에서 PDF 읽기
→ 페이지 텍스트 추출
→ 텍스트 chunk 생성
→ Gemini Embedding으로 chunk 벡터 생성
→ TiDB document_chunks.embedding_json에 저장

학생 질문
→ 질문을 Gemini Embedding으로 벡터화
→ TiDB에서 해당 강의 chunk embedding 조회
→ Python cosine similarity로 Top-K 검색
→ 검색된 강의 자료를 Gemini 2.5 Flash-Lite에 전달
→ 답변과 출처를 chat_messages, chat_message_sources에 저장
```

## 프로젝트 구조

```text
Back/
|-- webapp/                 # FastAPI 표현 계층
|   |-- main.py             # 앱 초기화, CORS, 예외 처리
|   `-- routers/            # API 라우터
|       |-- auth.py
|       |-- courses.py
|       |-- documents.py
|       |-- chat.py
|       |-- quests.py
|       |-- dashboard.py
|       |-- interventions.py
|       |-- course_messages.py
|       `-- router.py
|-- src/                    # 도메인 및 비즈니스 로직
|   |-- ai/                 # Gemini 호출, embedding, 답변 생성
|   |-- auth/               # 사용자 모델
|   |-- courses/            # 강의 모델
|   |-- enrollments/        # 수강 등록, 취약 개념
|   |-- documents/          # S3 저장, PDF 처리, RAG 색인
|   |-- chat/               # 채팅 모델
|   |-- quests/             # 퀘스트, 문항, 채점
|   |-- interventions/      # AI 자동 개입 제안
|   |-- analytics/          # 키워드/취약 개념 분석
|   `-- models/             # 공통 enum export
|-- dependencies/           # 인증/의존성 주입
|-- database/               # Async DB engine/session
|-- core/                   # 환경 설정, 보안, 이벤트 루프
|-- requirements.txt
|-- run_server.py
|-- .env.example
`-- README.md
```

## 주요 기능

### 인증

- 회원가입, 로그인, 로그아웃
- JWT 기반 인증
- 학생과 교강사 역할 분리

### 강의 관리

- 교강사 강의 생성
- 학생 입장 코드 기반 강의 참여
- 교강사/학생별 강의 목록 조회

### 강의 자료 관리

- PDF 업로드
- AWS S3 저장
- Presigned URL 기반 다운로드
- 주차/주제 메타데이터 설정
- 공개/비공개 전환
- 학생은 공개된 자료만 조회 가능

### RAG 채팅

- 학생 질문 저장
- 강의별 자료 chunk 검색
- Gemini 답변 생성
- 답변 출처 저장
- 질문 키워드 통계 누적
- 학생별 취약 개념 갱신

### 퀘스트

- 교강사 직접 퀘스트 생성
- 객관식 문항/보기/정답 저장
- 임시 저장과 발송 분리
- 학생 제출 및 자동 채점
- XP 지급 및 등급 갱신
- AI 퀘스트 초안 생성
- 완료 퀘스트 `completed` 상태 반환 및 중복 제출 차단

### 학생 성장 데이터

- 신규 수강생은 C등급, 0 XP에서 시작
- C등급: 0~599 누적 XP
- B등급: 600~1199 누적 XP
- A등급: 1200 XP 이상이며 최고 등급
- `GET /courses/{course_id}/me/stats`는 등급 내 진행 XP와 누적 XP를 함께 반환
- 퀘스트 오답과 채팅 기반 취약 개념을 AI 오답노트로 조회
- 오답노트는 문항, 내 답, 정답, 해설, 관련 퀘스트 또는 자료 출처를 포함

### AI 자동 제안

교강사용 대시보드에서 다음 세 가지 유형의 개입 제안을 제공합니다.

- 취약 개념 보충 퀘스트 발송
- C등급 또는 저참여 학생 대상 동기부여 메시지
- 반복 질문 키워드 기반 추가 자료 업로드 권고

각 제안은 채팅 질문 키워드, 퀘스트 참여율, 오답률, 취약 개념, 자료 준비 상태를 종합해 판단합니다.

## 주요 API

프론트엔드 계약에 맞춰 `/api/v1` prefix 없이 제공합니다.

### Auth

- `POST /auth/signup`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`

### Courses

- `GET /courses/me`
- `GET /courses/{course_id}`
- `POST /courses`
- `POST /courses/join`
- `DELETE /courses/{course_id}`

### Files

- `GET /courses/{course_id}/files`
- `POST /courses/{course_id}/files`
- `PATCH /courses/{course_id}/files/{file_id}`
- `PATCH /courses/{course_id}/files/{file_id}/publish`
- `DELETE /courses/{course_id}/files/{file_id}`

### Chat

- `GET /courses/{course_id}/chat`
- `POST /courses/{course_id}/chat`
- `POST /courses/{course_id}/chat/stream`

### Quests

- `GET /courses/{course_id}/quests`
- `GET /courses/{course_id}/quests/{quest_id}/content`
- `POST /courses/{course_id}/quests`
- `PUT /courses/{course_id}/quests/{quest_id}`
- `POST /courses/{course_id}/quests/{quest_id}/send`
- `DELETE /courses/{course_id}/quests/{quest_id}`
- `POST /courses/{course_id}/quests/{quest_id}/submit`
- `POST /courses/{course_id}/quests/ai-draft`

### Dashboard

- `GET /courses/{course_id}/analytics`
- `GET /courses/{course_id}/analytics/keywords`
- `GET /courses/{course_id}/analytics/students`
- `GET /courses/{course_id}/ai-proposals`
- `GET /courses/{course_id}/ai-config`
- `PUT /courses/{course_id}/ai-config`
- `GET /courses/{course_id}/me/stats`
- `GET /courses/{course_id}/me/weak-points`
- `GET /courses/{course_id}/notifications`
- `PATCH /courses/{course_id}/notifications/{notification_id}/read`
- `PATCH /courses/{course_id}/notifications/read-all`
- `POST /courses/{course_id}/quiz/submit`

## 환경 변수

실제 값은 `.env`에 작성합니다. `.env`는 Git에 올리지 않습니다.

```env
DATABASE_URL=mysql+aiomysql://<DB_USER>:<DB_PASSWORD>@<DB_HOST>:4000/<DB_NAME>
DATABASE_SSL=true

JWT_SECRET_KEY=<JWT_SECRET>

STORAGE_PROVIDER=s3
AWS_ACCESS_KEY_ID=<AWS_ACCESS_KEY_ID>
AWS_SECRET_ACCESS_KEY=<AWS_SECRET_ACCESS_KEY>
AWS_REGION=ap-northeast-2
S3_BUCKET_NAME=<S3_BUCKET_NAME>
S3_PREFIX=uploads
S3_PRESIGNED_URL_EXPIRES=3600

AI_PROVIDER=gemini
AI_MAX_OUTPUT_TOKENS=1024
GEMINI_API_KEY=<GEMINI_API_KEY>
GEMINI_MODEL=gemini-2.5-flash-lite

EMBEDDING_PROVIDER=gemini
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
GEMINI_EMBEDDING_OUTPUT_DIMENSIONALITY=768

LOCAL_RAG_ENABLED=true
RAG_CHUNK_SIZE=1200
RAG_CHUNK_OVERLAP=200
RAG_TOP_K=5
```

## 실행 방법

Windows 환경에서는 `uvicorn`을 직접 실행하지 않고 `run_server.py`를 사용합니다. TiDB 연결 시 Windows Proactor event loop 문제를 피하기 위해 Selector loop를 사용합니다.

```powershell
cd Back
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python run_server.py
```

서버 실행 후 확인:

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/health
http://127.0.0.1:8000/health/db
```

## 데이터베이스 추가 컬럼

기본 17개 테이블 외에 현재 백엔드 기능을 위해 다음 컬럼과 보조 테이블이 필요합니다.

```sql
ALTER TABLE enrollments
MODIFY current_rank ENUM('A', 'B', 'C') DEFAULT 'C';

ALTER TABLE course_documents
ADD COLUMN topic VARCHAR(255) NULL,
ADD COLUMN is_published BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE document_chunks
ADD COLUMN embedding_json LONGTEXT NULL,
ADD COLUMN embedding_model VARCHAR(100) NULL,
ADD COLUMN embedding_dim INT NULL;

CREATE INDEX idx_course_documents_course_id_deleted
ON course_documents(course_id, deleted_at);

CREATE TABLE IF NOT EXISTS notification_reads (
    notification_read_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    course_id BIGINT NOT NULL,
    notification_key VARCHAR(64) NOT NULL,
    read_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_notification_read_user_course_key (
        user_id,
        course_id,
        notification_key
    ),
    KEY idx_notification_reads_user_course (user_id, course_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (course_id) REFERENCES courses(course_id)
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    quiz_attempt_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    course_id BIGINT NOT NULL,
    enrollment_id BIGINT NOT NULL,
    message_key VARCHAR(64) NOT NULL,
    selected_index INT NOT NULL,
    is_correct BOOLEAN NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_quiz_attempt_enrollment_course_message (
        enrollment_id,
        course_id,
        message_key
    ),
    KEY idx_quiz_attempts_course_enrollment (course_id, enrollment_id),
    FOREIGN KEY (course_id) REFERENCES courses(course_id),
    FOREIGN KEY (enrollment_id) REFERENCES enrollments(enrollment_id)
);
```

`notification_reads`와 `quiz_attempts`는 앱 시작 시 없으면 자동 생성됩니다.

## 보안 정책

- `.env`는 Git에 포함하지 않습니다.
- AWS/Gemini/DB 키는 서버 환경 변수로만 관리합니다.
- S3 버킷은 Public Access Block을 유지합니다.
- 파일 다운로드는 S3 presigned URL로만 제공합니다.
- Presigned URL 기본 만료 시간은 1시간입니다.
- 학생은 본인이 수강 중인 강의의 공개 자료와 발송된 퀘스트만 접근할 수 있습니다.

## 검증 완료 항목

- Gemini 텍스트 생성
- Gemini embedding 768차원 생성
- S3 업로드, presigned 다운로드, 삭제
- PDF 한글 파일명 업로드
- S3 PDF 기반 RAG 색인
- TiDB `document_chunks.embedding_json` 저장
- RAG 채팅 답변 및 출처 반환
- 퀘스트 생성, 수정, 발송, 제출, 채점
- 신규 수강생 C등급 시작 및 XP 기반 등급 갱신
- A/B/C 등급 기준 및 등급 내 XP 진행도 반환
- 퀘스트 완료 상태 반환 및 중복 제출 방지
- 알림 읽음 상태 DB 저장
- AI 오답노트 문항/내 답/정답/해설 반환
- 채팅 퀴즈 결과 저장 및 통계 반영
- AI 퀘스트 초안 생성 시 주차별 자료 필터링
- Cloudflare Tunnel을 통한 HTTPS 프론트 연동

## 배포 메모

운영 배포 전에는 다음 설정을 권장합니다.

- Google Cloud 또는 AI Studio 예산 알림 설정
- AWS Budget 알림 설정
- S3 IAM 권한을 특정 버킷의 `uploads/*`로 제한
- `WEEKLY_INTERVENTION_INTERVAL_SECONDS=604800`
- `APP_DEBUG=false`
- 충분히 긴 `JWT_SECRET_KEY` 사용
