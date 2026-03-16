---
description: Triage and resolve incidents with the PersonalAsst bot
---

1. Check container status:
   ```
   docker compose ps
   ```
2. Check recent logs for errors:
   ```
   docker compose logs --tail=50 assistant
   ```
3. Check if bot process is responsive — send `/help` in Telegram.
4. If container crashed, check exit code:
   ```
   docker compose logs assistant | tail -20
   ```
5. If DB issue, verify PostgreSQL health:
   ```
   docker compose exec postgres pg_isready -U assistant
   ```
6. If memory/vector issue, check Qdrant:
   ```
   docker compose logs --tail=20 qdrant
   ```
7. If Google Workspace issue, check workspace-mcp logs:
   ```
   docker compose logs --tail=20 workspace-mcp
   ```
8. Query audit log for recent errors:
   ```
   docker compose exec postgres psql -U assistant -c "SELECT timestamp, agent_name, error FROM audit_log WHERE error IS NOT NULL ORDER BY timestamp DESC LIMIT 10;"
   ```
9. If cost cap hit, check daily_costs:
   ```
   docker compose exec postgres psql -U assistant -c "SELECT date, total_cost_usd, request_count FROM daily_costs ORDER BY date DESC LIMIT 5;"
   ```
10. Restart affected service:
    ```
    docker compose restart assistant
    ```
