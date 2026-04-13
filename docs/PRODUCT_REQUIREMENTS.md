# Custom-TA 제품 기획 및 기능 정의서

## 서비스 개요

Custom-TA는 교강사가 업로드한 강의 자료를 바탕으로 학생 질문에 답변하고, 학생의 학습 데이터를 분석해 맞춤형 퀘스트와 개입 제안을 제공하는 AI 조교 시스템입니다.

핵심 목표는 학생에게는 언제든 질문할 수 있는 보조 튜터를 제공하고, 교강사에게는 수업 운영에 필요한 학생 이해도 데이터를 제공하는 것입니다.

## 사용자 역할

### 교강사

- 강의 개설
- 강의 자료 업로드
- 자료 주차/주제 설정
- 자료 공개/비공개 전환
- 퀘스트 생성, 임시 저장, 수정, 발송
- AI 퀘스트 초안 생성
- 학생 분석 대시보드 조회
- AI 개입 제안 확인

### 학생

- 강의 코드로 강의 참여
- 공개된 강의 자료 조회 및 다운로드
- RAG 기반 AI 조교에게 질문
- 발송된 퀘스트 풀이
- XP와 등급 확인
- AI 오답노트 확인
- 알림 확인 및 읽음 처리

## 주요 사용자 시나리오

### 1. 강의 자료 기반 AI 질문

1. 교강사가 PDF 자료를 업로드합니다.
2. 백엔드는 PDF를 S3에 저장합니다.
3. 백그라운드에서 PDF 텍스트를 추출하고 chunk를 생성합니다.
4. Gemini Embedding으로 chunk embedding을 생성합니다.
5. embedding은 TiDB `document_chunks.embedding_json`에 저장됩니다.
6. 학생이 질문하면 질문 embedding과 문서 chunk embedding을 비교합니다.
7. 관련 chunk를 Gemini 답변 생성 프롬프트에 포함합니다.
8. 답변과 출처를 채팅 메시지 및 출처 테이블에 저장합니다.

### 2. 퀘스트 생성과 발송

1. 교강사가 직접 퀘스트를 작성하거나 AI 초안 생성을 요청합니다.
2. AI 초안 생성 시 주차가 선택되면 해당 주차 자료만 참고합니다.
3. 퀘스트는 먼저 `pending` 상태로 임시 저장됩니다.
4. 교강사가 수정 후 발송하면 `sent` 상태로 변경됩니다.
5. 발송 시 대상 학생 그룹에 따라 `student_quests`가 생성됩니다.

대상 그룹 규칙:

- 전체 수강생
- A 등급 학생
- B 등급 학생
- C 등급 학생
- B,C 등급 학생

### 3. 학생 퀘스트 풀이와 성장 데이터

1. 학생은 본인에게 배정된 퀘스트만 볼 수 있습니다.
2. 퀘스트 제출 시 자동 채점됩니다.
3. 정답률에 따라 XP가 지급됩니다.
4. 이미 제출한 퀘스트는 다시 제출할 수 없습니다.
5. 틀린 문항은 AI 오답노트에 기록됩니다.

등급 기준:

- C 등급: 0~599 누적 XP
- B 등급: 600~1199 누적 XP
- A 등급: 1200 XP 이상
- A 등급은 최고 등급입니다.

`GET /courses/{course_id}/me/stats` 응답 기준:

- `totalXp`: 전체 누적 XP
- `xp`: 현재 등급 안에서의 진행 XP
- `xpToNext`: 다음 등급까지 남은 XP
- A등급이면 `xpToNext`는 0입니다.

### 4. AI 오답노트

AI 오답노트는 학생이 틀린 퀘스트 문항과 채팅 기반 취약 개념을 모아 보여줍니다.

퀘스트 오답 기반 항목은 다음 정보를 포함합니다.

- 취약 문항
- 내 답
- 정답
- 해설
- 관련 퀘스트명

채팅 기반 항목은 질문과 답변에서 반복적으로 등장한 개념을 추적하고, 출처가 있으면 관련 강의 자료를 함께 제공합니다.

### 5. 교강사 분석 대시보드

교강사는 강의별로 다음 데이터를 확인합니다.

- 수강생 수
- 주간 질문 수
- 평균 참여율
- 평균 퀘스트 정답률
- 등급 분포
- 주차별 질문 키워드
- AI 개입 제안

AI 개입 제안은 다음 세 유형을 모두 독립적으로 판단합니다.

- 취약 개념 보충 퀘스트 발송
- 동기부여 메시지 발송
- 추가 자료 업로드 권고

해당 조건이 여러 개라면 여러 제안이 함께 생성될 수 있습니다.

## API 정책

### 인증

JWT Bearer Token을 사용합니다.

```http
Authorization: Bearer <JWT_TOKEN>
```

### 공통 에러 응답

```json
{
  "message": "에러 설명",
  "detail": "상세 정보"
}
```

### 주요 API

- `POST /auth/signup`
- `POST /auth/login`
- `GET /auth/me`
- `GET /courses/me`
- `POST /courses`
- `POST /courses/join`
- `GET /courses/{course_id}/files`
- `POST /courses/{course_id}/files`
- `GET /courses/{course_id}/chat`
- `POST /courses/{course_id}/chat`
- `GET /courses/{course_id}/quests`
- `POST /courses/{course_id}/quests`
- `PUT /courses/{course_id}/quests/{quest_id}`
- `POST /courses/{course_id}/quests/{quest_id}/send`
- `POST /courses/{course_id}/quests/{quest_id}/submit`
- `POST /courses/{course_id}/quests/ai-draft`
- `GET /courses/{course_id}/me/stats`
- `GET /courses/{course_id}/me/weak-points`
- `GET /courses/{course_id}/notifications`
- `PATCH /courses/{course_id}/notifications/{notification_id}/read`
- `PATCH /courses/{course_id}/notifications/read-all`
- `GET /courses/{course_id}/analytics`
- `GET /courses/{course_id}/analytics/keywords`
- `GET /courses/{course_id}/ai-proposals`

## 운영 및 배포 방향

현재 공모전 시연 환경은 다음 구조를 사용합니다.

```text
Vercel Frontend
→ Cloudflare Tunnel HTTPS
→ AWS EC2 FastAPI
→ TiDB / AWS S3 / Gemini API
```

운영 전환 시에는 임시 Cloudflare quick tunnel 대신 고정 도메인 기반 HTTPS 또는 정식 Cloudflare Tunnel named tunnel을 사용하는 것을 권장합니다.

