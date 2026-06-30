import os
from deepagents import create_deep_agent
from .tools import save_section, read_section, assemble_document


def build_agent():
    model = os.environ["MODEL"]

    subagents = [
        {
            "name": "intro_writer",
            "description": "Writes the introduction and scope for a technical markdown document.",
            "system_prompt": """
You write concise markdown for the introduction of a technical document.
Always return markdown only.
When appropriate, save output using save_section with filename 01-introduction.md.
""",
            "tools": [save_section],
        },
        {
            "name": "requirements_writer",
            "description": "Writes prerequisites, dependencies, assumptions, and environment variables.",
            "system_prompt": """
You write the requirements section for a technical document.
Always return markdown only.
When appropriate, save output using save_section with filename 02-requirements.md.
""",
            "tools": [save_section],
        },
        {
            "name": "architecture_writer",
            "description": "Writes the architecture section for coordinator and subagent responsibilities.",
            "system_prompt": """
You write the architecture section for a multi-agent system.
Always return markdown only.
Explain coordinator, subagents, and message flow.
When appropriate, save output using save_section with filename 03-architecture.md.
""",
            "tools": [save_section],
        },
        {
            "name": "deployment_writer",
            "description": "Writes Docker and runtime deployment instructions.",
            "system_prompt": """
You write deployment instructions for a Dockerized Python service.
Always return markdown only.
Include build and run commands.
When appropriate, save output using save_section with filename 04-deployment.md.
""",
            "tools": [save_section],
        },
        {
            "name": "reviewer",
            "description": "Reviews all generated sections for consistency and prepares the final assembly plan.",
            "system_prompt": """
You review generated markdown sections for consistency, duplication, and missing details.
Always return markdown only.
Use read_section when needed.
""",
            "tools": [read_section],
        },
    ]

    return create_deep_agent(
        model=model,
        tools=[save_section, read_section, assemble_document],
        subagents=subagents,
        system_prompt="""
You are the coordinator for a technical document generation system.

Your workflow:
1. Break the request into sections.
2. Delegate introduction to intro_writer.
3. Delegate requirements to requirements_writer.
4. Delegate architecture to architecture_writer.
5. Delegate deployment to deployment_writer.
6. Ask reviewer to inspect the generated sections.
7. Assemble the final document with assemble_document.
8. Return a concise completion message and mention the output filenames.

Preferred filenames:
- 01-introduction.md
- 02-requirements.md
- 03-architecture.md
- 04-deployment.md
- 05-final-document.md
""",
        name="document_coordinator",
    )
