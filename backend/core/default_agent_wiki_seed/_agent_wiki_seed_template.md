---
doc_id: example_default_agent_wiki_seed
kind: agent_wiki
title: Example Default Agent Wiki Seed
summary: 이 파일은 template라 설치되지 않습니다.
actor: system_seed
tags: ["default_seed", "template"]
schema_type: default_agent_wiki_seed_v1
---

## Purpose

이 문서는 seed 작성 형식 예시입니다. 실제 seed로 설치하려면 파일명을 `_`로 시작하지 않게 만들고 고유한 `doc_id`를 지정합니다.

## Maintained Notes

- runtime `flow-data`에 같은 `doc_id`가 있으면 덮어쓰지 않습니다.
- 운영 중 수정 가능한 지식은 Agent 지식 Wiki에서 수정합니다.
- core seed는 신규 설치와 누락 문서 보충을 위한 기본값입니다.
