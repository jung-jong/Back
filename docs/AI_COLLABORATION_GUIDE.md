# Custom-TA AI 협업 개발 지침서

## 문서 목적

본 문서는 Custom-TA 백엔드 개발 과정에서 AI 개발 에이전트와 협업하기 위해 사용한 작업 지침서입니다.
공모전 개발 기간 동안 AI를 단순 코드 생성 도구로만 사용하지 않고, 요구사항 정리, 아키텍처 설계, API 계약 검토, 오류 원인 분석, 배포 점검까지 함께 수행하기 위한 기준으로 작성했습니다.

## AI 협업 원칙

### 1. 프론트엔드 계약 우선

프론트엔드가 제공한 API 명세를 백엔드 구현의 기준으로 삼습니다.

- API prefix는 사용하지 않습니다.
- 인증은 `Authorization: Bearer <JWT_TOKEN>` 형식을 사용합니다.
- 에러 응답은 프론트가 읽을 수 있도록 `message` 필드를 우선 포함합니다.
- 프론트가 기대하는 camelCase 응답 필드를 유지합니다.
- 새 기능을 추가할 때는 기존 프론트 호출 방식을 깨지 않는 방향을 우선합니다.

### 2. 운영 확장성을 고려한 계층 분리

개발 초기에는 빠른 구현이 중요하지만, 이후 모델과 저장소를 교체할 수 있도록 계층을 분리합니다.

- `webapp/routers`: FastAPI 라우터와 HTTP 입출력
- `src/*/models.py`: SQLAlchemy ORM 모델
- `src/*/schemas.py`: Pydantic 요청/응답 스키마
- `src/*/service.py`: 핵심 비즈니스 로직
- `database`: DB 엔진, 세션, 런타임 테이블 관리
- `core`: 환경변수, 설정, 공통 옵션

### 3. 민감정보는 코드에 포함하지 않음

DB 주소, JWT Secret, Gemini API Key, AWS Access Key는 `.env`로 관리합니다.
`.env`는 Git에 포함하지 않으며, `.env.example`에는 placeholder만 둡니다.

### 4. 실제 사용자 흐름 기준 검증

단순히 함수가 동작하는지보다, 프론트 화면의 실제 흐름이 맞는지 확인합니다.

- 회원가입/로그인
- 강의 개설/참여
- 자료 업로드/공개
- RAG 채팅
- 퀘스트 생성/임시저장/수정/발송
- 학생 제출/채점/XP 반영
- 알림 읽음 상태 유지
- AI 오답노트 조회
- 교강사 대시보드 분석 조회

## AI에게 부여한 주요 역할

### 요구사항 분석자

사용자가 자연어로 전달한 요구사항과 프론트엔드 문서를 API 요구사항으로 변환합니다.

예시:

- "A등급한테만 보내" → `target_rule_type=RANK`, `target_rule_value=A`
- "B,C등급한테 보내" → `target_rule_type=RANK`, `target_rule_value=B,C`
- "새로고침하면 읽음이 풀림" → 읽음 상태를 DB에 저장하는 보조 테이블 필요

### 백엔드 설계자

TiDB에 이미 존재하는 17개 테이블을 기준으로 ORM과 서비스 로직을 작성합니다.
기능상 필요한 경우에만 보조 테이블을 추가합니다.

추가한 보조 테이블:

- `notification_reads`: 사용자별 알림 읽음 상태
- `quiz_attempts`: 채팅 내 퀴즈 정답/오답 기록

### 구현자

FastAPI 라우터, SQLAlchemy 쿼리, Gemini 연동, S3 파일 저장, RAG 검색, 퀘스트 채점 로직을 구현합니다.

### 검증자

변경 후 아래 항목을 확인합니다.

- Python 문법 컴파일
- API 응답 스키마
- DB 테이블 생성 여부
- S3 업로드/다운로드
- Gemini 텍스트 생성/임베딩 호출
- EC2 배포 후 health check

### 배포 보조자

EC2, systemd, Cloudflare Tunnel, Vercel 환경변수 연결 과정을 단계별로 점검합니다.

## 최종 기술 선택 근거

### LLM

Gemini 2.5 Flash-Lite를 사용합니다.
비용 효율이 좋고, 퀘스트 초안 생성과 RAG 답변 생성에 충분한 품질을 제공합니다.

### Embedding

Gemini Embedding 001을 사용합니다.
문서 chunk와 학생 질문을 같은 embedding 공간에 넣고, TiDB에 저장된 벡터를 Python cosine similarity로 검색합니다.

### Storage

AWS S3를 사용합니다.
원본 PDF 파일은 Git이나 EC2 로컬 디스크가 아니라 S3에 저장하고, 다운로드는 presigned URL로 제공합니다.

### Vector Store

별도 Pinecone 없이 TiDB를 사용합니다.
공모전 단계에서는 문서 규모가 크지 않기 때문에 TiDB의 `document_chunks.embedding_json`에 embedding을 저장하고 애플리케이션 레벨에서 유사도를 계산합니다.

## AI 협업 시 주의한 점

- 생성된 코드를 그대로 신뢰하지 않고 실제 에러 로그와 프론트 화면을 기준으로 재검증했습니다.
- 프론트엔드에서 발견한 문제를 백엔드 데이터 흐름으로 역추적했습니다.
- 임시 구현이 필요한 경우에도 이후 운영 구조로 자연스럽게 확장될 수 있도록 인터페이스를 유지했습니다.
- 공모전 심사 기간 동안 빠르게 시연 가능한 배포 방식을 우선하되, 보안상 민감정보는 코드에 남기지 않았습니다.

