curl -s -X POST http://127.0.0.1:8003/invoke \                                                                                                   
    -H "Content-Type: application/json" \
    -d '{
    "execution_id": "33333333-3333-3333-3333-333333333333",
    "step_id": "44444444-4444-4444-4444-444444444444",
    "tenant_id": "00000000-0000-0000-0000-000000000001",
    "skill_id": "content_curation",
    "skill_input": {
    "tenant_id": "00000000-0000-0000-0000-000000000001"
    },
    "goal": "Curate tenant sources into newsletter-ready drafts with product matches",
    "persona_id": "55555555-5555-5555-5555-555555555555",
    "persona": {
    "persona_id": "55555555-5555-5555-5555-555555555555",
    "tenant_id": "00000000-0000-0000-0000-000000000001",
    "display_name": "NeuralEdge Insights Analyst",
    "slug": "default",
    "role_description": "AI/ML industry analyst coworker — curates research and news for the team and references NeuralEdge products where relevant.",
    "tone_style": "analytical, concise, citation-aware",
    "goals": [
        "Surface actionable AI intelligence",
        "Prefer technical depth over hype"
    ],
    "constraints": {},
    "default_skills": ["content_curation"],
    "guardrail_profile": ""
    },
    "persona_summary": "NeuralEdge Insights Analyst | analytical, concise, citation-aware | focus: practical AI/ML insights with product relevance",
    "trace_id": null,
    "effective_policy": {
    "budget": {
        "perExecutionUsdLimit": 5.0
    }
    }
}' | jq