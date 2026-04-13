# PersonalAsst - Enhanced with PaperClip-Inspired Orchestration

> Note: This document provides orchestration context. For the latest production-ready hardening, security, and operations updates, refer to `README.md`, `docs/RUNBOOK.md`, and `docs/CHANGELOG.md`.

## Overview

PersonalAsst has been enhanced with PaperClip-inspired agent orchestration capabilities, transforming it from a single-user assistant into a multi-agent orchestration platform while maintaining its security-first Docker deployment model.

## What's New

### 🚀 Multi-Agent Orchestration
- **Agent Registry**: Define agents with roles, capabilities, and hierarchies
- **Task Management**: Create, assign, and track tasks with goal ancestry
- **Governance Layer**: Human oversight with approval gates and budget controls
- **Web Dashboard**: React-based UI for managing agents and tasks

### 🏢 Organizational Structure
- **Org Chart**: Visual hierarchy of agents (CEO, CTO, Developers, Specialists)
- **Roles & Responsibilities**: Each agent has defined capabilities and budget
- **Cost Control**: Per-agent budgets with enforcement at infrastructure level
- **Audit Trail**: Complete logging of all decisions and actions

### 🎯 Key Features
- **Atomic Execution**: No duplicate work through task checkout system
- **Persistent State**: Agents maintain context across sessions
- **Goal-Aware Tasks**: Every task carries its full goal ancestry
- **Real-time Updates**: WebSocket-based live dashboard
- **Mobile Ready**: Access orchestration from any device

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Telegram UI    │    │   Web Dashboard  │    │   Mobile UI     │
│   (Existing)     │    │     (New)       │    │   (Future)      │
└─────────┬───────┘    └─────────┬────────┘    └─────────┬───────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Orchestration API     │
                    │   (FastAPI + SQLAlchemy) │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │    Agent Registry       │
                    │    Task Manager          │
                    │    Governance Layer      │
                    └────────────┬────────────┘
                                 │
          ┌────────────────┼────────────────┐
          │                │                │
    ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
    │   Atlas   │  │  Agents   │  │   Tasks   │
    │ (Orchestrator)│  │ (Multi)   │  │ (Queue)   │
    └───────────┘  └───────────┘  └───────────┘
```

## Quick Start

### 1. Deploy with Docker Compose

```bash
# Clone and setup
git clone <repository>
cd PersonalAsst

# Copy environment file
cp .env.example .env
# Edit .env with your API keys

# Start all services
docker compose up -d
```

Services started:
- **assistant**: Main PersonalAsst bot (port varies)
- **orchestration-api**: Orchestration API server (port 8000)
- **orchestration-ui**: Web dashboard (port 3000)
- **postgres**: Database
- **redis**: Cache/sessions
- **qdrant**: Vector storage
- **workspace-mcp**: Google Workspace integration

### 2. Access the Interfaces

#### Telegram Bot (Existing)
- Continue using your existing Telegram bot
- All current commands work unchanged
- Organization lifecycle is available via `/orgs` (`create`, `info`, `pause`, `resume`, `delete`)

#### Web Dashboard (New)
- Open http://localhost:3001
- View agent org chart
- Create and manage tasks
- Monitor costs and performance
- Real-time updates

#### API (New)
- REST API at http://localhost:8000
- OpenAPI docs at http://localhost:8000/docs
- WebSocket endpoint at ws://localhost:8000/ws

### 3. Create Your First Agent Team

```python
# Example: Create a development team
import asyncio
from src.orchestration.agent_registry import AgentRegistry, AgentDefinition, AgentRole

async def setup_dev_team():
    registry = AgentRegistry(get_session_factory())
    
    # Create CTO
    cto = await registry.create_agent(AgentDefinition(
        company_id="default",
        name="Atlas CTO",
        role=AgentRole.CTO,
        description="Technical lead overseeing development",
        capabilities=["architecture", "code_review", "technical_decisions"],
        monthly_budget=300.0
    ))
    
    # Create Developers
    dev1 = await registry.create_agent(AgentDefinition(
        company_id="default",
        name="Code Developer 1",
        role=AgentRole.DEVELOPER,
        description="Full-stack developer",
        capabilities=["coding", "testing", "debugging"],
        parent_agent_id=cto.id,
        monthly_budget=150.0
    ))
    
    dev2 = await registry.create_agent(AgentDefinition(
        company_id="default",
        name="Code Developer 2",
        role=AgentRole.DEVELOPER,
        description="Frontend specialist",
        capabilities=["react", "ui_ux", "testing"],
        parent_agent_id=cto.id,
        monthly_budget=150.0
    ))
    
    return [cto, dev1, dev2]

# Run the setup
team = asyncio.run(setup_dev_team())
print(f"Created team with {len(team)} agents")
```

## Agent Roles

### CEO (Chief Executive)
- Oversees all operations
- Strategic decision making
- Budget approval
- Can hire/fire other agents

### CTO (Chief Technology)
- Technical leadership
- Architecture decisions
- Code review oversight
- Manages development team

### Developer
- Code implementation
- Testing and debugging
- Feature development
- Reports to CTO

### Analyst
- Data analysis
- Research tasks
- Reporting
- Can work independently

### Coordinator
- Task coordination
- Resource allocation
- Progress tracking
- Cross-team communication

### Specialist
- Domain-specific expertise
- Specialized tools (Drive, Gmail, etc.)
- Focused capabilities
- Reports to relevant lead

### Assistant
- General assistance
- User support
- Basic tasks
- Learning and growth

## Task Management

### Goal Ancestry
Every task carries its full goal ancestry:
```
["company:mission", "project:feature", "task:implement"]
```

This gives agents context about why they're doing something, not just what.

### Task Lifecycle
1. **Created**: Task defined with goal ancestry
2. **Pending**: Waiting for assignment
3. **Assigned**: Checked out by specific agent
4. **In Progress**: Agent working on task
5. **Completed**: Task finished with result
6. **Failed**: Task failed with error

### Atomic Execution
- Tasks are checked out atomically
- No two agents work on the same task
- Prevents duplicate work and wasted compute

## Cost Control

### Per-Agent Budgets
- Each agent has monthly budget
- Soft warning at 80% utilization
- Hard stop at 100% (requires override)
- Real-time cost tracking

### Budget Enforcement
```python
# Agent budget enforcement happens at infrastructure level
if agent.current_month_spend >= agent.monthly_budget:
    raise BudgetExceeded(f"Agent {agent.id} exceeded budget")
```

## Security Model

### Maintained from Original
- **Docker Containerization**: All services in containers
- **Input/Output Guardrails**: Safety checks on all I/O
- **Cost Caps**: Per-user and per-agent limits
- **User Allowlist**: Only authorized users
- **No Secrets in Code**: All via environment variables

### New Security Features
- **Agent Isolation**: Agents can't access each other's data
- **Task Authorization**: Only assigned agents can access tasks
- **Audit Logging**: Complete audit trail of all actions
- **Governance Controls**: Human approval required for critical actions

## API Examples

### Create Agent
```bash
curl -X POST http://localhost:8000/api/companies/default/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Security Specialist",
    "role": "specialist",
    "description": "Handles security audits and compliance",
    "capabilities": ["security_audit", "compliance_check"],
    "monthly_budget": 200.0
  }'
```

### Create Task
```bash
curl -X POST http://localhost:8000/api/companies/default/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Security Audit",
    "description": "Perform quarterly security audit",
    "goal_ancestry": ["company:mission", "project:security", "task:audit"],
    "priority": "high",
    "budget_allocated": 50.0
  }'
```

### Assign Task
```bash
curl -X POST http://localhost:8000/api/tasks/{task_id}/assign \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "security-specialist-id"}'
```

## Migration from Single-Agent

### Backward Compatibility
- Existing Telegram interface unchanged
- All current commands continue working
- Gradual migration path available

### Migration Steps
1. **Phase 1**: Deploy orchestration services
2. **Phase 2**: Create agent organization
3. **Phase 3**: Migrate tasks to multi-agent
4. **Phase 4**: Optimize agent workflows

## Monitoring

### Dashboard Metrics
- Agent utilization
- Task completion rates
- Cost tracking
- Performance metrics

### Health Checks
```bash
# API health
curl http://localhost:8000/api/health

# Service status
docker compose ps
```

### Logs
```bash
# Orchestration logs
docker compose logs orchestration-api

# Dashboard logs
docker compose logs orchestration-ui
```

## Development

### Local Development
```bash
# Start orchestration API only
cd src/orchestration
python -m uvicorn api:app --reload --host 0.0.0.0 --port 8000

# Start React UI
cd orchestration-ui
npm start
```

### Database Migrations
```bash
# Apply new orchestration tables
docker compose exec assistant alembic upgrade head
```

### Testing
```bash
# Run orchestration tests
python -m pytest tests/test_orchestration.py -v

# Run integration tests
python -m pytest tests/test_integration.py -v
```

## Roadmap

### Phase 1 (Current)
- ✅ Basic agent registry
- ✅ Task management
- ✅ Web dashboard
- ✅ Cost control
- ✅ Governance layer

### Phase 2 (Planned)
- 🔄 Agent skill learning
- 🔄 Advanced scheduling
- 🔄 Multi-company support
- 🔄 Mobile app

### Phase 3 (Future)
- ⏳ AI-powered agent optimization
- ⏳ Marketplace for agent templates
- ⏳ Advanced analytics
- ⏳ Voice interface

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

### Areas for Contribution
- Agent skill definitions
- UI/UX improvements
- Performance optimizations
- Documentation
- Testing

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Support

- **Documentation**: [docs/](docs/)
- **Issues**: [GitHub Issues](https://github.com/llores28/PersonalAsst_v1/issues)
- **Discussions**: [GitHub Discussions](https://github.com/llores28/PersonalAsst_v1/discussions)

## Acknowledgments

- Inspired by [PaperClip](https://github.com/paperclipai/paperclip) for orchestration concepts
- Built with FastAPI, React, and Material-UI
- Powered by OpenAI and other AI providers
