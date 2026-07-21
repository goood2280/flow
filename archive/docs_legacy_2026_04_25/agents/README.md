# Agent Specs

각 에이전트는 자유롭게 추론하더라도, 입출력은 항상 명시된 계약을 따른다.

공통 원칙:
- 입력은 `AgentTaskRequest`
- 출력은 `AgentTaskResult`
- 실행안은 `ActionProposal`
- 자유서술보다 `summary + findings + metrics + output` 구조를 우선

목록:
- [orchestrator.md](orchestrator.md)
- [data_adapter_agent.md](data_adapter_agent.md)
- [source_contract_review_agent.md](source_contract_review_agent.md)
- [step_auto_classification_agent.md](step_auto_classification_agent.md)
- [clean_split_review_agent.md](clean_split_review_agent.md)
- [process_ml_review_agent.md](process_ml_review_agent.md)
- [et_report_review_agent.md](et_report_review_agent.md)
- [plan_generation_agent.md](plan_generation_agent.md)
- [publish_mail_agent.md](publish_mail_agent.md)
- [publish_json_agent.md](publish_json_agent.md)
