# Custom-TA AI 협업 개발 로그

## 협업 개요

본 로그는 Custom-TA 백엔드를 개발하면서 AI 개발 에이전트와 어떤 방식으로 협업했는지 정리한 기록입니다.
개발 과정은 요구사항 분석, API 계약 정렬, 구현, 오류 수정, 배포, 프론트엔드 연동 검증 순서로 진행했습니다.

## 1단계: 데이터베이스와 ORM 매핑

초기에는 TiDB에 이미 생성된 17개 테이블을 기준으로 SQLAlchemy ORM 모델을 작성했습니다.

주요 작업:

- `users`, `courses`, `enrollments` 모델 정의
- `course_documents`, `document_chunks` 모델 정의
- `chat_sessions`, `chat_messages`, `chat_message_sources` 모델 정의
- `quests`, `quest_questions`, `quest_question_choices` 모델 정의
- `student_quests`, `student_quest_answers` 모델 정의
- `weak_concepts`, `course_keyword_stats`, `ai_interventions`, `course_messages` 모델 정의

설계 판단:

- 기존 DDL의 snake_case 컬럼명을 그대로 유지했습니다.
- PK는 BigInt autoincrement 기준으로 매핑했습니다.
- Enum은 Python Enum과 SQLAlchemy Enum으로 맞췄습니다.

## 2단계: 인증과 강의 API

프론트엔드 명세에 맞춰 JWT 기반 인증을 구현했습니다.

구현 API:

- `POST /auth/signup`
- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`
- `GET /courses/me`
- `POST /courses`
- `POST /courses/join`

중요 수정:

- 신규 학생 수강 등록 시 기본 등급을 B가 아니라 C로 시작하도록 수정했습니다.
- 프론트가 문자열 ID를 기대하므로 응답의 `id` 필드는 문자열로 반환했습니다.

## 3단계: 파일 저장과 RAG

초기에는 로컬 저장소를 사용했고, 배포 준비 단계에서 AWS S3로 전환했습니다.

최종 구조:

```text
PDF 업로드
→ S3 저장
→ PDF 텍스트 추출
→ chunk 생성
→ Gemini Embedding 생성
→ TiDB embedding_json 저장
→ 질문 embedding과 cosine similarity 검색
→ Gemini 답변 생성
→ 출처 저장
```

검증 항목:

- S3 업로드
- presigned URL 다운로드
- PDF 한글 파일명 처리
- Gemini embedding 768차원 생성
- RAG 답변과 출처 반환

## 4단계: Gemini 전환

로컬 Ollama 기반 테스트 이후 배포를 위해 Gemini API로 전환했습니다.

사용 모델:

- 답변 생성: `gemini-2.5-flash-lite`
- AI 초안 생성: `gemini-2.5-flash-lite`
- AI 자동 제안: `gemini-2.5-flash-lite`
- 문서/질문 embedding: `gemini-embedding-001`

설계 판단:

- 답변 생성 모델과 embedding 모델은 역할이 다르므로 분리했습니다.
- embedding은 `RETRIEVAL_DOCUMENT`, `RETRIEVAL_QUERY` task type을 구분했습니다.

## 5단계: 퀘스트와 XP

프론트엔드의 퀘스트 직접 생성 UI에 맞춰 문항 배열 저장을 지원했습니다.

지원 기능:

- 객관식 보기 개수 가변 지원
- 정답 인덱스 저장
- 임시 저장과 발송 분리
- 퀘스트 수정 시 기존 문항 복원
- 제출 후 자동 채점
- 중복 제출 차단
- 완료 상태 `completed` 반환

XP 및 등급 기준:

- C: 0~599 누적 XP
- B: 600~1199 누적 XP
- A: 1200 XP 이상

프론트 표시를 위해 `xp`, `xpToNext`, `totalXp`를 구분했습니다.

## 6단계: 알림과 오답노트

프론트 테스트 중 새로고침 시 알림 읽음 상태가 풀리는 문제가 확인되었습니다.

해결:

- `notification_reads` 보조 테이블 추가
- 단일 읽음 처리 DB 저장
- 전체 읽음 처리 DB 저장
- 사용자별 read 상태 반환

오답노트 개선:

- 퀘스트 오답 문항 기반으로 취약 개념 저장
- 오답노트 응답에 문항, 내 답, 정답, 해설, 관련 퀘스트명 포함
- 채팅 기반 취약 개념은 출처가 있을 경우 관련 자료 표시
- 테스트 과정에서 쌓인 일반 단어성 CHAT 오답 키워드 정리

## 7단계: 교강사 대시보드와 AI 자동 제안

교강사 대시보드는 학생 질문, 퀘스트 참여율, 오답률, 취약 개념을 기반으로 분석 데이터를 제공합니다.

구현 API:

- `GET /courses/{course_id}/analytics`
- `GET /courses/{course_id}/analytics/keywords`
- `GET /courses/{course_id}/analytics/students`
- `GET /courses/{course_id}/ai-proposals`
- `GET /courses/{course_id}/ai-config`
- `PUT /courses/{course_id}/ai-config`

AI 자동 제안은 세 종류를 독립 판단합니다.

- SEND_QUEST
- SEND_MESSAGE
- UPLOAD_MATERIAL

## 8단계: 배포

배포 구조:

```text
Vercel Frontend
→ Cloudflare Tunnel
→ AWS EC2
→ FastAPI systemd service
→ TiDB / S3 / Gemini
```

EC2에서 FastAPI는 systemd 서비스로 실행합니다.
Cloudflare Tunnel은 tmux 세션에서 유지합니다.

운영 확인 명령:

```bash
sudo systemctl status custom-ta
tmux ls
pgrep -af cloudflared
curl https://<cloudflare-tunnel-url>/health
```

## 주요 문제 해결 기록

### TiDB 연결 문제

Windows 환경에서 async MySQL 드라이버 연결 문제가 발생해 Windows Selector Event Loop를 사용하도록 조정했습니다.

### S3 리다이렉트 문제

S3 presigned URL 요청에서 리전 리다이렉트가 발생해 regional endpoint를 명시했습니다.

### 퀘스트 대상 그룹 문제

프론트에서 A/B/C/B,C 등급을 선택해도 전체 학생에게 발송되는 문제가 있었습니다.

해결:

- `targetGroup` 문자열을 백엔드에서 파싱
- `target_rule_type=RANK`
- `target_rule_value=A`, `B`, `C`, `B,C`
- 발송 시 `student_quests`를 해당 등급 학생에게만 생성
- 학생 퀘스트 보관함도 본인에게 배정된 퀘스트만 조회하도록 수정

### XP 표시 문제

누적 XP를 그대로 `xp`로 내려주면 등급이 오른 뒤 진행바가 깨지는 문제가 있었습니다.

해결:

- `totalXp`: 누적 XP
- `xp`: 현재 등급 내 진행 XP
- `xpToNext`: 다음 등급까지 남은 XP

### 알림 읽음 상태 문제

프론트 optimistic update만으로는 새로고침 후 read 상태가 유지되지 않았습니다.

해결:

- `notification_reads` 테이블에 사용자별 읽음 상태 저장

## 결론

Custom-TA 백엔드는 AI 개발 에이전트와의 협업을 통해 빠른 구현뿐 아니라 프론트엔드 계약, 데이터 영속성, 운영 배포, UX 문제까지 반복적으로 검증하며 완성도를 높였습니다.
본 프로젝트에서 AI는 코드 작성 보조자뿐 아니라 요구사항 분석자, 오류 원인 추적자, 배포 점검자, 문서화 파트너로 활용되었습니다.

