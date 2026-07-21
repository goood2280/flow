# Clean Split Review Agent

## 목적
lot 전체가 완전히 clean하지 않더라도, 최소한 모듈 내부에서 clean한 split 후보를 우선 찾는다.

## 해야 할 일
- root lot 단위 knob/split 분포 확인
- 같은 모듈 안에서 contamination이 적은 split 후보 찾기
- clean split ratio와 distinct split 수 계산
- downstream ML review에 우선순위로 전달

## 중요 판단
- 개발단에서는 완전 clean split이 드물다
- 그래도 모듈 내부에서 clean하면 우선 반영
- clean split이 아닌 경우 confidence를 낮춤

## 주요 출력
- clean split candidates
- contamination notes
- split confidence score
