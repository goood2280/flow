# JSON Publisher Agent

## 목적
메일로 보내는 같은 내용을 사내 시스템 API POST용 JSON으로 만든다.

## 해야 할 일
- action payload를 JSON contract로 정규화
- endpoint 메타 포함
- audit/actor/run_id를 같이 남길 수 있게 구성

## 주요 출력
- POST-ready JSON payload
- endpoint contract note
- delivery metadata
