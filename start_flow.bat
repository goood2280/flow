@echo off
cd /d "%~dp0"
title flow web server (port 8080)
REM 이 호스트(16GB)는 small 프로파일 기본 STRICT 가드가 Polars RSS 잔류(할당자 arena/
REM mmap 캐시)만 보고 "메모리 크다"로 큰 작업을 오탐 거절한다. STRICT=0 으로 두면
REM 하드캡(rss>=limit)은 유지하되, 소프트밴드는 실제 시스템 여유 메모리를 기준으로만
REM 거절한다. 양산(대용량) 서버는 FLOW_RESOURCE_PROFILE=full 도 가능.
set FLOW_PROCESS_MEMORY_LIMIT_STRICT=0
python -m uvicorn app:app --host 0.0.0.0 --port 8080
pause
