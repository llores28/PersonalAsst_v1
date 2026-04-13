import React, { useState, useEffect, useCallback } from 'react';
import {
  Container, Grid, Paper, Typography, Box, Card, CardContent,
  List, ListItem, ListItemText, Chip, Button, Dialog, DialogTitle,
  DialogContent, DialogActions, TextField, MenuItem, IconButton,
  Tooltip, Tab, Tabs, Alert, LinearProgress,
} from '@mui/material';
import {
  AttachMoney, Build, Schedule, TrendingUp, Psychology,
  Add, Refresh, FolderSpecial, Chat, Warning, CheckCircle,
  Delete, PauseCircle, PlayCircle, Sync, School, Edit, Science,
  Settings, HealthAndSafety, Timer, PlayArrow, AccountTree, Stop,
  BugReport, WorkHistory, Timeline,
} from '@mui/icons-material';
import Drawer from '@mui/material/Drawer';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip as RTooltip } from 'recharts';
import axios from 'axios';

const API = '/api';

function Dashboard() {
  const [tab, setTab] = useState(0);
  const [summary, setSummary] = useState(null);
  const [costs, setCosts] = useState([]);
  const [tools, setTools] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [activity, setActivity] = useState([]);
  const [persona, setPersona] = useState(null);
  const [orgs, setOrgs] = useState([]);
  const [skills, setSkills] = useState([]);
  const [budget, setBudget] = useState(null);
  const [bgJobs, setBgJobs] = useState([]);
  const [repairs, setRepairs] = useState([]);
  const [traceDrawer, setTraceDrawer] = useState({ open: false, sessionKey: null, steps: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [orgDialogOpen, setOrgDialogOpen] = useState(false);
  const [selectedOrg, setSelectedOrg] = useState(null);

  const fetchAll = useCallback(async () => {
    try {
      const [sumR, costR, toolR, schedR, actR, persR, orgR, skillsR, budgetR, bgR, repR] = await Promise.all([
        axios.get(`${API}/dashboard`),
        axios.get(`${API}/costs?days=30`),
        axios.get(`${API}/tools`),
        axios.get(`${API}/schedules`),
        axios.get(`${API}/activity?limit=30`),
        axios.get(`${API}/persona`),
        axios.get(`${API}/orgs`),
        axios.get(`${API}/skills`),
        axios.get(`${API}/budget`),
        axios.get(`${API}/background-jobs`).catch(() => ({ data: [] })),
        axios.get(`${API}/repairs`).catch(() => ({ data: [] })),
      ]);
      setSummary(sumR.data);
      setCosts(costR.data);
      setTools(toolR.data);
      setSchedules(schedR.data);
      setActivity(actR.data);
      setPersona(persR.data);
      setOrgs(orgR.data);
      setSkills(skillsR.data);
      setBudget(budgetR.data);
      setBgJobs(bgR.data);
      setRepairs(repR.data);
      setError(null);
    } catch (e) {
      setError('Failed to connect to Atlas Dashboard API');
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 10000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  const handleCreateOrg = async (data) => {
    try {
      await axios.post(`${API}/orgs`, data);
      setOrgDialogOpen(false);
      fetchAll();
    } catch (e) { console.error(e); }
  };

  if (loading) {
    return (
      <Container><Box display="flex" justifyContent="center" alignItems="center" minHeight="100vh">
        <Box textAlign="center"><LinearProgress sx={{ mb: 2, width: 200 }} /><Typography>Connecting to Atlas...</Typography></Box>
      </Box></Container>
    );
  }

  return (
    <Container maxWidth="xl" sx={{ mt: 3, mb: 4 }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={3}>
        <Typography variant="h4" fontWeight={700}>Atlas Dashboard</Typography>
        <Box>
          <Chip label={summary?.costs ? `$${summary.costs.month_usd.toFixed(2)} this month` : '--'} color="primary" variant="outlined" sx={{ mr: 1 }} />
          <IconButton onClick={fetchAll}><Refresh /></IconButton>
        </Box>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {/* Summary Cards */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={6} md={2}>
          <SummaryCard icon={<AttachMoney />} label="Today" value={`$${summary?.costs?.today_usd?.toFixed(2) || '0.00'}`} color="#4caf50" />
        </Grid>
        <Grid item xs={6} md={2}>
          <SummaryCard icon={<Chat />} label="Interactions" value={summary?.interactions_today || 0} color="#2196f3" />
        </Grid>
        <Grid item xs={6} md={2}>
          <SummaryCard icon={<Build />} label="Tools" value={summary?.tool_count || 0} color="#ff9800" />
        </Grid>
        <Grid item xs={6} md={2}>
          <SummaryCard icon={<Schedule />} label="Schedules" value={summary?.active_schedules || 0} color="#9c27b0" />
        </Grid>
        <Grid item xs={6} md={2}>
          <SummaryCard icon={<TrendingUp />} label="Quality" value={summary?.quality?.average?.toFixed(2) || '--'} color={getQualityColor(summary?.quality)} />
        </Grid>
        <Grid item xs={6} md={2}>
          <SummaryCard icon={<FolderSpecial />} label="Projects" value={summary?.org_count || 0} color="#607d8b" />
        </Grid>
        <Grid item xs={6} md={2}>
          <SummaryCard icon={<School />} label="Skills" value={skills?.length || 0} color="#795548" />
        </Grid>
      </Grid>

      {/* Tab Navigation */}
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }} variant="scrollable" scrollButtons="auto">
        <Tab label="Overview" />
        <Tab label="Organizations" />
        <Tab label="Activity" />
        <Tab label="Tools" />
        <Tab label="Skills" />
        <Tab label="Repairs" icon={repairs.filter(r => !r.auto_applied && r.status === 'open').length > 0 ? <BugReport color="warning" fontSize="small" /> : undefined} iconPosition="end" />
        <Tab label="Jobs" icon={bgJobs.filter(j => j.status === 'running').length > 0 ? <WorkHistory color="info" fontSize="small" /> : undefined} iconPosition="end" />
        <Tab label="System" />
      </Tabs>

      {/* Tab Content */}
      {tab === 0 && <OverviewTab costs={costs} tools={tools} schedules={schedules} persona={persona} quality={summary?.quality} budget={budget} fetchAll={fetchAll} />}
      {tab === 1 && <OrgsTab orgs={orgs} onCreateOrg={() => setOrgDialogOpen(true)} onSelectOrg={setSelectedOrg} fetchAll={fetchAll} />}
      {tab === 2 && <ActivityTab activity={activity} onViewTrace={setTraceDrawer} />}
      {tab === 3 && <ToolsTab tools={tools} fetchAll={fetchAll} />}
      {tab === 4 && <SkillsTab skills={skills} fetchAll={fetchAll} />}
      {tab === 5 && <RepairsTab repairs={repairs} fetchAll={fetchAll} />}
      {tab === 6 && <BackgroundJobsTab jobs={bgJobs} fetchAll={fetchAll} />}
      {tab === 7 && <SchedulerDiagnosticsTab />}

      <TraceDrawer open={traceDrawer.open} steps={traceDrawer.steps} sessionKey={traceDrawer.sessionKey} onClose={() => setTraceDrawer({ open: false, sessionKey: null, steps: [] })} />

      {/* Create Org Dialog */}
      <Dialog open={orgDialogOpen} onClose={() => setOrgDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>New Organization</DialogTitle>
        <DialogContent><OrgForm onSubmit={handleCreateOrg} /></DialogContent>
        <DialogActions><Button onClick={() => setOrgDialogOpen(false)}>Cancel</Button></DialogActions>
      </Dialog>

      {/* Org Detail Dialog */}
      {selectedOrg && <OrgDetailDialog org={selectedOrg} onClose={() => setSelectedOrg(null)} fetchAll={fetchAll} />}
    </Container>
  );
}


// ── Summary Card ──────────────────────────────────────────────────────

function SummaryCard({ icon, label, value, color }) {
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
        <Box display="flex" alignItems="center">
          <Box sx={{ color, mr: 1.5, display: 'flex' }}>{icon}</Box>
          <Box>
            <Typography variant="caption" color="text.secondary">{label}</Typography>
            <Typography variant="h6" fontWeight={600}>{value}</Typography>
          </Box>
        </Box>
      </CardContent>
    </Card>
  );
}

function getQualityColor(q) {
  if (!q?.average) return '#9e9e9e';
  if (q.average >= 0.8) return '#4caf50';
  if (q.average >= 0.6) return '#ff9800';
  return '#f44336';
}


// ── Overview Tab ──────────────────────────────────────────────────────

function OverviewTab({ costs, tools, schedules, persona, quality, budget, fetchAll }) {
  return (
    <Grid container spacing={3}>
      {/* Cost Chart */}
      <Grid item xs={12} md={8}>
        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" mb={2}>Cost Trend (30 days)</Typography>
          {costs.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={costs}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <RTooltip />
                <Line type="monotone" dataKey="cost_usd" stroke="#4caf50" strokeWidth={2} dot={false} name="Cost ($)" />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <Typography color="text.secondary">No cost data yet</Typography>
          )}
        </Paper>
      </Grid>

      {/* Quality */}
      <Grid item xs={12} md={4}>
        <Paper sx={{ p: 2, height: '100%' }}>
          <Box display="flex" alignItems="center" gap={1} mb={1}>
            <Typography variant="h6">Quality</Typography>
            <Tooltip title="Quality is scored 0–1 based on how accurately Atlas routes requests to the right tools and agents. Each interaction is evaluated by the reflector and averaged over the last 20 responses. Higher is better.">
              <Box component="span" sx={{ cursor: 'help', color: 'text.secondary', fontSize: 16 }}>ⓘ</Box>
            </Tooltip>
          </Box>
          {quality?.recent_scores?.length > 0 ? (
            <>
              <Box display="flex" alignItems="baseline" mb={1}>
                <Typography variant="h3" fontWeight={700} color={getQualityColor(quality)}>
                  {quality.average?.toFixed(2)}
                </Typography>
                <Typography variant="body2" color="text.secondary" ml={1}>/ 1.00</Typography>
              </Box>
              <Chip
                label={quality.trend || 'stable'}
                size="small"
                color={quality.trend === 'improving' ? 'success' : quality.trend === 'declining' ? 'error' : 'default'}
                sx={{ mb: 1.5 }}
              />
              <ResponsiveContainer width="100%" height={100}>
                <LineChart data={quality.recent_scores.map((s, i) => ({ i, score: s }))}>
                  <Line type="monotone" dataKey="score" stroke="#2196f3" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
              <Box mt={1.5} sx={{ borderTop: '1px solid #eee', pt: 1 }}>
                <Typography variant="caption" color="text.secondary" fontWeight={600}>Score guide:</Typography>
                <Box display="flex" flexDirection="column" gap={0.3} mt={0.5}>
                  <Box display="flex" alignItems="center" gap={1}>
                    <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#4caf50', flexShrink: 0 }} />
                    <Typography variant="caption" color="text.secondary">≥ 0.80 — Excellent (ideal routing)</Typography>
                  </Box>
                  <Box display="flex" alignItems="center" gap={1}>
                    <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#ff9800', flexShrink: 0 }} />
                    <Typography variant="caption" color="text.secondary">0.60–0.79 — Good (minor misroutes)</Typography>
                  </Box>
                  <Box display="flex" alignItems="center" gap={1}>
                    <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#f44336', flexShrink: 0 }} />
                    <Typography variant="caption" color="text.secondary">{'< 0.60 — Needs attention'}</Typography>
                  </Box>
                </Box>
              </Box>
            </>
          ) : (
            <Box>
              <Typography color="text.secondary" mb={1.5}>No quality data yet — score appears after your first interactions.</Typography>
              <Box sx={{ borderTop: '1px solid #eee', pt: 1 }}>
                <Typography variant="caption" color="text.secondary" fontWeight={600}>Score guide:</Typography>
                <Box display="flex" flexDirection="column" gap={0.3} mt={0.5}>
                  <Box display="flex" alignItems="center" gap={1}>
                    <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#4caf50', flexShrink: 0 }} />
                    <Typography variant="caption" color="text.secondary">≥ 0.80 — Excellent</Typography>
                  </Box>
                  <Box display="flex" alignItems="center" gap={1}>
                    <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#ff9800', flexShrink: 0 }} />
                    <Typography variant="caption" color="text.secondary">0.60–0.79 — Good</Typography>
                  </Box>
                  <Box display="flex" alignItems="center" gap={1}>
                    <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#f44336', flexShrink: 0 }} />
                    <Typography variant="caption" color="text.secondary">{'< 0.60 — Needs attention'}</Typography>
                  </Box>
                </Box>
              </Box>
            </Box>
          )}
        </Paper>
      </Grid>

      {/* Tools */}
      <Grid item xs={12} md={6}>
        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" mb={1}>Registered Tools</Typography>
          {tools.length === 0 ? (
            <Typography color="text.secondary">No custom tools registered yet</Typography>
          ) : (
            <List dense>
              {tools.map(t => (
                <ListItem key={t.id}>
                  <ListItemText
                    primary={<Box display="flex" alignItems="center">{t.name} <Chip label={t.tool_type} size="small" sx={{ ml: 1 }} /></Box>}
                    secondary={`${t.description} — used ${t.use_count}x`}
                  />
                  <Chip label={t.is_active ? 'active' : 'disabled'} size="small" color={t.is_active ? 'success' : 'default'} />
                </ListItem>
              ))}
            </List>
          )}
        </Paper>
      </Grid>

      {/* Schedules — full management panel */}
      <Grid item xs={12} md={6}>
        <SchedulesPanel schedules={schedules} fetchAll={fetchAll} />
      </Grid>

      {/* Budget */}
      {budget && (
        <Grid item xs={12}>
          <BudgetPanel budget={budget} />
        </Grid>
      )}

      {/* Persona */}
      <Grid item xs={12}>
        <Paper sx={{ p: 2 }}>
          <Box display="flex" alignItems="center" mb={1}>
            <Psychology sx={{ mr: 1 }} />
            <Typography variant="h6">Persona</Typography>
          </Box>
          {persona ? (
            <Box>
              <Typography><strong>{persona.assistant_name}</strong> v{persona.version} — {persona.interviews_completed} interviews completed</Typography>
              {persona.personality?.ocean && (
                <Box mt={1} display="flex" gap={2} flexWrap="wrap">
                  {Object.entries(persona.personality.ocean).map(([trait, val]) => (
                    <Box key={trait} sx={{ minWidth: 100 }}>
                      <Typography variant="caption" textTransform="capitalize">{trait}</Typography>
                      <LinearProgress variant="determinate" value={val * 100} sx={{ height: 8, borderRadius: 4 }} />
                      <Typography variant="caption">{(val * 100).toFixed(0)}%</Typography>
                    </Box>
                  ))}
                </Box>
              )}
            </Box>
          ) : (
            <Typography color="text.secondary">No persona configured yet</Typography>
          )}
        </Paper>
      </Grid>
    </Grid>
  );
}


// ── Budget Panel ──────────────────────────────────────────────────────

function BudgetPanel({ budget }) {
  const barColor = (pct) => {
    if (pct >= 100) return '#f44336';
    if (pct >= 80) return '#ff9800';
    return '#4caf50';
  };

  const BudgetRow = ({ label, spent, cap, pct, requests }) => (
    <Box mb={1.5}>
      <Box display="flex" justifyContent="space-between" alignItems="baseline" mb={0.5}>
        <Typography variant="body2" fontWeight={500}>{label}</Typography>
        <Box display="flex" alignItems="baseline" gap={1}>
          <Typography variant="body2" fontWeight={600} color={barColor(pct)}>
            ${spent.toFixed(4)}
          </Typography>
          <Typography variant="caption" color="text.secondary">/ ${cap.toFixed(2)} cap</Typography>
          <Typography variant="caption" color="text.disabled">({pct}%)</Typography>
        </Box>
      </Box>
      <Box sx={{ height: 8, bgcolor: '#e0e0e0', borderRadius: 4, overflow: 'hidden' }}>
        <Box
          sx={{
            height: '100%',
            width: `${Math.min(pct, 100)}%`,
            bgcolor: barColor(pct),
            borderRadius: 4,
            transition: 'width 0.4s ease',
          }}
        />
      </Box>
      {requests > 0 && (
        <Typography variant="caption" color="text.disabled">{requests} request{requests !== 1 ? 's' : ''}</Typography>
      )}
    </Box>
  );

  return (
    <Paper sx={{ p: 2 }}>
      <Box display="flex" alignItems="center" gap={1} mb={2}>
        <AttachMoney sx={{ color: '#4caf50' }} />
        <Typography variant="h6">Budget</Typography>
        <Tooltip title="Daily and monthly spending limits are set via DAILY_COST_CAP_USD and MONTHLY_COST_CAP_USD in your .env file.">
          <Box component="span" sx={{ cursor: 'help', color: 'text.secondary', fontSize: 16 }}>ⓘ</Box>
        </Tooltip>
      </Box>
      <Grid container spacing={3}>
        <Grid item xs={12} md={6}>
          <BudgetRow
            label="Today"
            spent={budget.today_usd}
            cap={budget.daily_cap_usd}
            pct={budget.daily_pct}
            requests={budget.request_count_today}
          />
        </Grid>
        <Grid item xs={12} md={6}>
          <BudgetRow
            label="This month"
            spent={budget.month_usd}
            cap={budget.monthly_cap_usd}
            pct={budget.monthly_pct}
            requests={budget.request_count_month}
          />
        </Grid>
      </Grid>
      {(budget.daily_pct >= 80 || budget.monthly_pct >= 80) && (
        <Alert severity={budget.daily_pct >= 100 || budget.monthly_pct >= 100 ? 'error' : 'warning'} sx={{ mt: 1 }}>
          {budget.daily_pct >= 100
            ? 'Daily cap reached — new requests are blocked until midnight.'
            : budget.monthly_pct >= 100
            ? 'Monthly cap reached — new requests are blocked until next month.'
            : `Approaching limit — ${budget.daily_pct >= 80 ? `daily at ${budget.daily_pct}%` : `monthly at ${budget.monthly_pct}%`}. Adjust caps in .env if needed.`}
        </Alert>
      )}
    </Paper>
  );
}


// ── Schedules Panel ───────────────────────────────────────────────────

function SchedulesPanel({ schedules, fetchAll }) {
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [editingSchedule, setEditingSchedule] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [editForm, setEditForm] = useState({ description: '', trigger_type: 'cron', cron: {}, interval: {} });

  const handleDelete = async (id, description) => {
    if (!window.confirm(`Delete schedule:\n"${description}"?\n\nThis will permanently remove it from the scheduler.`)) return;
    try {
      await axios.delete(`${API}/schedules/${id}`);
      fetchAll();
    } catch (e) {
      console.error('Delete failed', e);
      alert('Delete failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleTogglePause = async (s) => {
    const endpoint = s.is_active ? 'pause' : 'resume';
    try {
      await axios.post(`${API}/schedules/${s.id}/${endpoint}`);
      fetchAll();
    } catch (e) {
      console.error('Toggle failed', e);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    setSyncResult(null);
    try {
      const r = await axios.post(`${API}/schedules/sync`);
      setSyncResult(r.data);
      fetchAll();
    } catch (e) {
      setSyncResult({ error: e.response?.data?.detail || e.message });
    } finally {
      setSyncing(false);
    }
  };

  const handleEdit = (schedule) => {
    setEditingSchedule(schedule);
    setEditForm({
      description: schedule.description,
      trigger_type: schedule.trigger_type,
      cron: schedule.trigger_config?.cron || { hour: 9, minute: 0, day_of_week: '*' },
      interval: schedule.trigger_config?.interval || { seconds: 3600 },
    });
    setTestResult(null);
  };

  const handleSaveEdit = async () => {
    if (!editingSchedule) return;
    try {
      const triggerConfig = {
        trigger_type: editForm.trigger_type,
        [editForm.trigger_type]: editForm[editForm.trigger_type],
      };
      await axios.put(`${API}/schedules/${editingSchedule.id}`, {
        description: editForm.description,
        trigger_config: triggerConfig,
      });
      setEditingSchedule(null);
      fetchAll();
    } catch (e) {
      alert('Update failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleTest = async (schedule) => {
    setTestResult({ loading: true, scheduleId: schedule.id });
    try {
      const r = await axios.post(`${API}/schedules/${schedule.id}/test`);
      setTestResult({ success: true, ...r.data });
      fetchAll();
    } catch (e) {
      setTestResult({ error: true, message: e.response?.data?.detail || e.message });
    }
  };

  return (
    <Paper sx={{ p: 2 }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
        <Typography variant="h6">Scheduled Tasks</Typography>
        <Tooltip title="Sync with APScheduler — cleans orphaned/fired jobs and refreshes next run time">
          <span>
            <Button
              size="small"
              startIcon={<Sync />}
              onClick={handleSync}
              disabled={syncing}
              variant="outlined"
            >
              {syncing ? 'Syncing…' : 'Sync'}
            </Button>
          </span>
        </Tooltip>
      </Box>

      {syncResult && (
        <Alert
          severity={syncResult.error ? 'error' : 'success'}
          sx={{ mb: 1, fontSize: 12 }}
          onClose={() => setSyncResult(null)}
        >
          {syncResult.error
            ? `Sync error: ${syncResult.error}`
            : `Sync done — ${syncResult.live_job_count} live jobs, ${syncResult.fired_once_deleted?.length || 0} one-shots removed, ${syncResult.orphaned_paused?.length || 0} orphans paused, ${syncResult.next_run_at_synced} next-run times updated`
          }
        </Alert>
      )}

      {schedules.length === 0 ? (
        <Typography color="text.secondary">No scheduled tasks</Typography>
      ) : (
        <List dense disablePadding>
          {schedules.map(s => (
            <ListItem
              key={s.id}
              divider
              alignItems="flex-start"
              secondaryAction={
                <Box display="flex" gap={0.5}>
                  <Tooltip title="Run now (test)">
                    <IconButton
                      size="small"
                      onClick={() => handleTest(s)}
                      color="primary"
                      disabled={testResult?.loading && testResult?.scheduleId === s.id}
                    >
                      <PlayArrow fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Edit schedule">
                    <IconButton
                      size="small"
                      onClick={() => handleEdit(s)}
                      color="info"
                    >
                      <Edit fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title={s.is_active ? 'Pause' : 'Resume'}>
                    <IconButton
                      size="small"
                      onClick={() => handleTogglePause(s)}
                      color={s.is_active ? 'warning' : 'success'}
                    >
                      {s.is_active ? <PauseCircle fontSize="small" /> : <PlayCircle fontSize="small" />}
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Delete permanently">
                    <IconButton
                      size="small"
                      onClick={() => handleDelete(s.id, s.description)}
                      color="error"
                    >
                      <Delete fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
              }
              sx={{ pr: 10 }}
            >
              <ListItemText
                primary={
                  <Box display="flex" alignItems="center" gap={1} flexWrap="wrap">
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>{s.description}</Typography>
                    <Chip label={s.is_active ? 'active' : 'paused'} size="small" color={s.is_active ? 'success' : 'default'} />
                    <Chip label={s.trigger_type} size="small" variant="outlined" />
                  </Box>
                }
                secondary={
                  <Typography variant="caption" color="text.secondary">
                    next: {s.next_run_at ? new Date(s.next_run_at).toLocaleString() : 'N/A'}
                    {s.last_run_at ? ` · last: ${new Date(s.last_run_at).toLocaleString()}` : ''}
                  </Typography>
                }
              />
            </ListItem>
          ))}
        </List>
      )}

      {/* Edit Dialog */}
      <Dialog open={!!editingSchedule} onClose={() => setEditingSchedule(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Edit Schedule</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Description"
            value={editForm.description}
            onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
            sx={{ mb: 2, mt: 1 }}
          />

          <TextField
            select
            fullWidth
            label="Trigger Type"
            value={editForm.trigger_type}
            onChange={(e) => setEditForm({ ...editForm, trigger_type: e.target.value })}
            sx={{ mb: 2 }}
          >
            <MenuItem value="cron">Cron (specific times)</MenuItem>
            <MenuItem value="interval">Interval (repeating)</MenuItem>
            <MenuItem value="once">Once (one-time)</MenuItem>
          </TextField>

          {editForm.trigger_type === 'cron' && (
            <Box display="flex" gap={1}>
              <TextField
                label="Hour (0-23)"
                type="number"
                value={editForm.cron.hour || 0}
                onChange={(e) => setEditForm({ ...editForm, cron: { ...editForm.cron, hour: parseInt(e.target.value) } })}
                sx={{ flex: 1 }}
              />
              <TextField
                label="Minute (0-59)"
                type="number"
                value={editForm.cron.minute || 0}
                onChange={(e) => setEditForm({ ...editForm, cron: { ...editForm.cron, minute: parseInt(e.target.value) } })}
                sx={{ flex: 1 }}
              />
              <TextField
                label="Day of week"
                value={editForm.cron.day_of_week || '*'}
                onChange={(e) => setEditForm({ ...editForm, cron: { ...editForm.cron, day_of_week: e.target.value } })}
                sx={{ flex: 1 }}
                helperText="* = all, 0-6 = Sun-Sat"
              />
            </Box>
          )}

          {editForm.trigger_type === 'interval' && (
            <TextField
              fullWidth
              label="Interval (seconds)"
              type="number"
              value={editForm.interval.seconds || 3600}
              onChange={(e) => setEditForm({ ...editForm, interval: { seconds: parseInt(e.target.value) } })}
              helperText="3600 = 1 hour, 86400 = 1 day"
            />
          )}

          {testResult && testResult.scheduleId === editingSchedule?.id && (
            <Alert severity={testResult.error ? 'error' : 'success'} sx={{ mt: 2 }}>
              {testResult.error ? testResult.message : `✅ Test executed: ${testResult.message}`}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => { setEditingSchedule(null); setTestResult(null); }}>Cancel</Button>
          <Button onClick={handleSaveEdit} variant="contained">Save Changes</Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
}


// ── Organizations Tab ─────────────────────────────────────────────────

function OrgsTab({ orgs, onCreateOrg, onSelectOrg, fetchAll }) {
  const handleToggleOrg = async (event, org) => {
    event.stopPropagation();
    try {
      const endpoint = org.status === 'active' ? 'pause' : 'resume';
      await axios.post(`${API}/orgs/${org.id}/${endpoint}`);
      fetchAll();
    } catch (e) {
      alert('Organization update failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleDeleteOrg = async (event, org) => {
    event.stopPropagation();
    if (!window.confirm(`Delete organization "${org.name}"? This will also remove its agents, tasks, and activity log.`)) return;
    try {
      await axios.delete(`${API}/orgs/${org.id}`);
      fetchAll();
    } catch (e) {
      alert('Organization delete failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  return (
    <Box>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h6">Organizations</Typography>
        <Button variant="contained" startIcon={<Add />} onClick={onCreateOrg}>New Organization</Button>
      </Box>
      {orgs.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <FolderSpecial sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
          <Typography color="text.secondary">No organizations yet. Create one to start managing specialized agent teams.</Typography>
        </Paper>
      ) : (
        <Grid container spacing={2}>
          {orgs.map(org => (
            <Grid item xs={12} md={4} key={org.id}>
              <Card sx={{ cursor: 'pointer', '&:hover': { boxShadow: 4 } }} onClick={() => onSelectOrg(org)}>
                <CardContent>
                  <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
                    <Typography variant="h6">{org.name}</Typography>
                    <Chip label={org.status} size="small" color={org.status === 'active' ? 'success' : 'default'} />
                  </Box>
                  {org.goal && <Typography variant="body2" color="text.secondary" mb={1}>{org.goal}</Typography>}
                  <Box display="flex" gap={1}>
                    <Chip label={`${org.agent_count} agents`} size="small" variant="outlined" />
                    <Chip label={`${org.task_count} tasks`} size="small" variant="outlined" />
                    <Chip label={`${org.completed_tasks} done`} size="small" variant="outlined" color="success" />
                  </Box>
                  <Box display="flex" justifyContent="flex-end" gap={0.5} mt={2}>
                    <Tooltip title={org.status === 'active' ? 'Deactivate organization' : 'Reactivate organization'}>
                      <IconButton size="small" onClick={(event) => handleToggleOrg(event, org)} color={org.status === 'active' ? 'warning' : 'success'}>
                        {org.status === 'active' ? <PauseCircle fontSize="small" /> : <PlayCircle fontSize="small" />}
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Delete organization">
                      <IconButton size="small" onClick={(event) => handleDeleteOrg(event, org)} color="error">
                        <Delete fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Box>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}
    </Box>
  );
}


// ── Activity Tab ──────────────────────────────────────────────────────

function ActivityTab({ activity, onViewTrace }) {
  const handleViewTrace = async (a) => {
    try {
      const sessionKey = `agent_session:${a.telegram_id || ''}`;
      const res = await axios.get(`${API}/traces`, { params: { session_key: sessionKey, limit: 50 } });
      onViewTrace({ open: true, sessionKey, steps: res.data });
    } catch (e) { console.error(e); }
  };

  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="h6" mb={2}>Recent Activity</Typography>
      {activity.length === 0 ? (
        <Typography color="text.secondary">No activity recorded yet</Typography>
      ) : (
        <List dense>
          {activity.map(a => (
            <ListItem key={a.id} divider
              secondaryAction={
                <Tooltip title="View agent thought trace">
                  <IconButton size="small" onClick={() => handleViewTrace(a)}><Timeline fontSize="small" /></IconButton>
                </Tooltip>
              }
            >
              <ListItemText
                primary={
                  <Box display="flex" alignItems="center" gap={1}>
                    {a.error ? <Warning fontSize="small" color="error" /> : <CheckCircle fontSize="small" color="success" />}
                    <Typography variant="body2">{a.message_preview || `[${a.direction}]`}</Typography>
                  </Box>
                }
                secondary={
                  <Box display="flex" gap={2} mt={0.5} flexWrap="wrap">
                    <Typography variant="caption">{a.timestamp ? new Date(a.timestamp).toLocaleString() : ''}</Typography>
                    {a.agent_name && <Chip label={a.agent_name} size="small" />}
                    {a.model_used && <Chip label={a.model_used} size="small" variant="outlined" />}
                    {a.cost_usd != null && <Typography variant="caption">${a.cost_usd.toFixed(4)}</Typography>}
                    {a.duration_ms != null && <Typography variant="caption">{a.duration_ms}ms</Typography>}
                  </Box>
                }
              />
            </ListItem>
          ))}
        </List>
      )}
    </Paper>
  );
}


// ── Trace Drawer ──────────────────────────────────────────────────────

function TraceDrawer({ open, steps, sessionKey, onClose }) {
  return (
    <Drawer anchor="right" open={open} onClose={onClose} PaperProps={{ sx: { width: 520, p: 3 } }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h6">Agent Thought Trace</Typography>
        <IconButton onClick={onClose}><Stop /></IconButton>
      </Box>
      {sessionKey && <Typography variant="caption" color="text.secondary" mb={2} display="block">Session: {sessionKey}</Typography>}
      {steps.length === 0 ? (
        <Typography color="text.secondary">No trace steps recorded for this session yet.</Typography>
      ) : (
        <Box>
          {steps.map((step, idx) => (
            <Box key={step.id} sx={{ mb: 2, pl: 2, borderLeft: '3px solid', borderColor: step.tool_result_preview ? 'success.light' : 'primary.light' }}>
              <Box display="flex" alignItems="center" gap={1} mb={0.5}>
                <Chip label={`#${step.step_index + 1}`} size="small" />
                {step.agent_name && <Chip label={step.agent_name} size="small" color="primary" variant="outlined" />}
                {step.tool_name && <Chip label={step.tool_name} size="small" color="secondary" />}
                {step.duration_ms != null && <Typography variant="caption" color="text.secondary">{step.duration_ms}ms</Typography>}
              </Box>
              {step.tool_args && (
                <Box sx={{ bgcolor: 'grey.100', borderRadius: 1, p: 1, mb: 0.5, fontSize: 11, fontFamily: 'monospace', overflowX: 'auto' }}>
                  {JSON.stringify(step.tool_args, null, 2).slice(0, 300)}
                </Box>
              )}
              {step.tool_result_preview && (
                <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'monospace', display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  ↳ {step.tool_result_preview}
                </Typography>
              )}
              <Typography variant="caption" color="text.disabled">{step.timestamp ? new Date(step.timestamp).toLocaleTimeString() : ''}</Typography>
            </Box>
          ))}
        </Box>
      )}
    </Drawer>
  );
}


// ── Repairs Tab ───────────────────────────────────────────────────────

function RepairsTab({ repairs, fetchAll }) {
  const riskColor = (r) => r === 'low' ? 'success' : r === 'medium' ? 'warning' : 'error';
  const statusColor = (s) => s === 'deployed' ? 'success' : s === 'open' || s === 'plan_ready' ? 'warning' : s === 'verification_failed' ? 'error' : 'default';

  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="h6" mb={2}>Repair Tickets</Typography>
      {repairs.length === 0 ? (
        <Typography color="text.secondary">No repair tickets yet.</Typography>
      ) : (
        <List dense>
          {repairs.map(r => (
            <ListItem key={r.id} divider
              secondaryAction={
                <Box display="flex" gap={1}>
                  {r.auto_applied && <Chip label="Auto-applied" size="small" color="success" icon={<CheckCircle />} />}
                  {!r.auto_applied && r.status === 'open' && <Chip label="Pending approval" size="small" color="warning" icon={<Warning />} />}
                </Box>
              }
            >
              <ListItemText
                primary={
                  <Box display="flex" alignItems="center" gap={1}>
                    <BugReport fontSize="small" color={statusColor(r.status)} />
                    <Typography variant="body2" fontWeight={500}>{r.title}</Typography>
                  </Box>
                }
                secondary={
                  <Box display="flex" gap={1} mt={0.5} flexWrap="wrap">
                    <Chip label={r.status} size="small" color={statusColor(r.status)} />
                    <Chip label={`Risk: ${r.risk_level}`} size="small" color={riskColor(r.risk_level)} variant="outlined" />
                    <Chip label={r.priority} size="small" variant="outlined" />
                    <Typography variant="caption" color="text.secondary">{r.created_at ? new Date(r.created_at).toLocaleString() : ''}</Typography>
                  </Box>
                }
              />
            </ListItem>
          ))}
        </List>
      )}
    </Paper>
  );
}


// ── Background Jobs Tab ───────────────────────────────────────────────

function BackgroundJobsTab({ jobs, fetchAll }) {
  const handleCancel = async (jobId) => {
    try {
      await axios.patch(`${API}/background-jobs/${jobId}/cancel`);
      fetchAll();
    } catch (e) { console.error(e); }
  };

  const statusColor = (s) => s === 'running' ? 'info' : s === 'done' ? 'success' : s === 'failed' ? 'error' : 'default';

  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="h6" mb={2}>Background Jobs</Typography>
      {jobs.length === 0 ? (
        <Box>
          <Typography color="text.secondary">No background jobs yet.</Typography>
          <Typography variant="caption" color="text.secondary" display="block" mt={1}>
            Say things like "Monitor my inbox and alert me when…" or "Keep watching my calendar until…" to start one.
          </Typography>
        </Box>
      ) : (
        <List dense>
          {jobs.map(j => (
            <ListItem key={j.id} divider
              secondaryAction={
                j.status === 'running' && (
                  <Tooltip title="Cancel job">
                    <IconButton size="small" color="error" onClick={() => handleCancel(j.id)}><Stop /></IconButton>
                  </Tooltip>
                )
              }
            >
              <ListItemText
                primary={
                  <Box display="flex" alignItems="center" gap={1}>
                    <WorkHistory fontSize="small" color={statusColor(j.status)} />
                    <Typography variant="body2" fontWeight={500} sx={{ wordBreak: 'break-word', maxWidth: 340 }}>{j.goal}</Typography>
                  </Box>
                }
                secondary={
                  <Box mt={0.5}>
                    <Box display="flex" gap={1} flexWrap="wrap" mb={0.5}>
                      <Chip label={j.status} size="small" color={statusColor(j.status)} />
                      <Typography variant="caption" color="text.secondary">
                        {j.iterations_run}/{j.max_iterations} ticks
                      </Typography>
                      {j.done_condition && <Chip label={`Until: ${j.done_condition.slice(0, 40)}`} size="small" variant="outlined" />}
                    </Box>
                    {j.status === 'running' && (
                      <LinearProgress variant="determinate" value={Math.round(j.iterations_run / j.max_iterations * 100)} sx={{ height: 4, borderRadius: 2, maxWidth: 300 }} />
                    )}
                    {j.result && <Typography variant="caption" color="text.secondary" display="block" mt={0.5} sx={{ fontStyle: 'italic' }}>{j.result.slice(0, 120)}</Typography>}
                    <Typography variant="caption" color="text.disabled">{j.created_at ? new Date(j.created_at).toLocaleString() : ''}</Typography>
                  </Box>
                }
              />
            </ListItem>
          ))}
        </List>
      )}
    </Paper>
  );
}


// ── Org Form ──────────────────────────────────────────────────────────

function OrgForm({ onSubmit }) {
  const [form, setForm] = useState({ name: '', description: '', goal: '' });
  const handleSubmit = (e) => { e.preventDefault(); onSubmit(form); };
  return (
    <Box component="form" onSubmit={handleSubmit} sx={{ mt: 1 }}>
      <TextField fullWidth label="Name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} margin="normal" required />
      <TextField fullWidth label="Goal" value={form.goal} onChange={e => setForm({ ...form, goal: e.target.value })} margin="normal" placeholder="e.g., Find a new job in AI/ML" />
      <TextField fullWidth label="Description" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} margin="normal" multiline rows={2} />
      <Button type="submit" variant="contained" sx={{ mt: 2 }}>Create</Button>
    </Box>
  );
}


// ── Org Detail Dialog ─────────────────────────────────────────────────

function OrgDetailDialog({ org, onClose, fetchAll }) {
  const [agents, setAgents] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [activityLog, setActivityLog] = useState([]);
  const [agentForm, setAgentForm] = useState(false);
  const [taskForm, setTaskForm] = useState(false);
  const [editingAgent, setEditingAgent] = useState(null);
  const [editingTask, setEditingTask] = useState(null);

  const fetchOrgData = useCallback(async () => {
    try {
      const [aR, tR, actR] = await Promise.all([
        axios.get(`${API}/orgs/${org.id}/agents`),
        axios.get(`${API}/orgs/${org.id}/tasks`),
        axios.get(`${API}/orgs/${org.id}/activity?limit=20`),
      ]);
      setAgents(aR.data);
      setTasks(tR.data);
      setActivityLog(actR.data);
    } catch (e) { console.error(e); }
  }, [org.id]);

  useEffect(() => { fetchOrgData(); }, [fetchOrgData]);

  const handleAddAgent = async (data) => {
    await axios.post(`${API}/orgs/${org.id}/agents`, data);
    setAgentForm(false);
    fetchOrgData();
    fetchAll();
  };

  const handleAddTask = async (data) => {
    await axios.post(`${API}/orgs/${org.id}/tasks`, data);
    setTaskForm(false);
    fetchOrgData();
    fetchAll();
  };

  const handleUpdateAgent = async (agentId, data) => {
    await axios.patch(`${API}/orgs/${org.id}/agents/${agentId}`, data);
    setEditingAgent(null);
    fetchOrgData();
    fetchAll();
  };

  const handleDeleteAgent = async (agent) => {
    if (!window.confirm(`Delete agent "${agent.name}"?`)) return;
    await axios.delete(`${API}/orgs/${org.id}/agents/${agent.id}`);
    setEditingAgent(null);
    fetchOrgData();
    fetchAll();
  };

  const handleUpdateTask = async (taskId, data) => {
    await axios.patch(`${API}/orgs/${org.id}/tasks/${taskId}`, data);
    setEditingTask(null);
    fetchOrgData();
    fetchAll();
  };

  const handleDeleteTask = async (task) => {
    if (!window.confirm(`Delete task "${task.title}"?`)) return;
    await axios.delete(`${API}/orgs/${org.id}/tasks/${task.id}`);
    setEditingTask(null);
    fetchOrgData();
    fetchAll();
  };

  const handleCompleteTask = async (taskId) => {
    await axios.post(`${API}/orgs/${org.id}/tasks/${taskId}/complete`);
    fetchOrgData();
    fetchAll();
  };

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Box>
            <Typography variant="h6">{org.name}</Typography>
            <Typography variant="caption" color="text.secondary">Org ID: {org.id}</Typography>
          </Box>
          <Chip label={org.status} color={org.status === 'active' ? 'success' : 'default'} />
        </Box>
        {org.goal && <Typography variant="body2" color="text.secondary">{org.goal}</Typography>}
      </DialogTitle>
      <DialogContent>
        {/* Agents */}
        <Box mb={3}>
          <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
            <Typography variant="subtitle1" fontWeight={600}>Agents ({agents.length})</Typography>
            <Button size="small" startIcon={<Add />} onClick={() => setAgentForm(!agentForm)}>Add Agent</Button>
          </Box>
          {agentForm && <OrgAgentForm onSubmit={handleAddAgent} onCancel={() => setAgentForm(false)} />}
          {editingAgent && (
            <OrgAgentForm
              initialValue={editingAgent}
              submitLabel="Save"
              onSubmit={(data) => handleUpdateAgent(editingAgent.id, data)}
              onCancel={() => setEditingAgent(null)}
            />
          )}
          <List dense>
            {agents.map(a => (
              <AgentListItem
                key={a.id}
                agent={a}
                onEdit={() => setEditingAgent(a)}
                onDelete={() => handleDeleteAgent(a)}
              />
            ))}
          </List>
          {agents.length === 0 && !agentForm && <Typography variant="body2" color="text.secondary">No agents yet</Typography>}
        </Box>

        {/* Tasks */}
        <Box mb={3}>
          <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
            <Typography variant="subtitle1" fontWeight={600}>Tasks ({tasks.length})</Typography>
            <Button size="small" startIcon={<Add />} onClick={() => setTaskForm(!taskForm)}>Add Task</Button>
          </Box>
          {taskForm && <OrgTaskForm agents={agents} onSubmit={handleAddTask} onCancel={() => setTaskForm(false)} />}
          {editingTask && (
            <OrgTaskForm
              agents={agents}
              initialValue={editingTask}
              submitLabel="Save"
              onSubmit={(data) => handleUpdateTask(editingTask.id, data)}
              onCancel={() => setEditingTask(null)}
            />
          )}
          <List dense>
            {tasks.map(t => (
              <ListItem key={t.id} divider secondaryAction={<Box>
                {t.status !== 'completed' && (
                  <Tooltip title="Mark Complete"><IconButton size="small" onClick={() => handleCompleteTask(t.id)} color="success"><CheckCircle /></IconButton></Tooltip>
                )}
                <Tooltip title="Edit task"><IconButton size="small" onClick={() => setEditingTask(t)}><Edit fontSize="small" /></IconButton></Tooltip>
                <Tooltip title="Delete task"><IconButton size="small" color="error" onClick={() => handleDeleteTask(t)}><Delete fontSize="small" /></IconButton></Tooltip>
              </Box>}>
                <ListItemText
                  primary={<Box display="flex" alignItems="center" gap={1}>
                    <Typography variant="body2">{t.title}</Typography>
                    <Chip label={`ID ${t.id}`} size="small" variant="outlined" />
                    <Chip label={t.priority} size="small" color={t.priority === 'high' ? 'error' : t.priority === 'critical' ? 'error' : 'default'} />
                    <Chip label={t.status} size="small" color={t.status === 'completed' ? 'success' : 'primary'} variant="outlined" />
                  </Box>}
                  secondary={t.description}
                />
              </ListItem>
            ))}
          </List>
        </Box>

        {/* Activity */}
        {activityLog.length > 0 && (
          <Box>
            <Typography variant="subtitle1" fontWeight={600} mb={1}>Recent Activity</Typography>
            <List dense>
              {activityLog.slice(0, 10).map(a => (
                <ListItem key={a.id}>
                  <ListItemText
                    primary={<Typography variant="body2">{a.action}: {a.details}</Typography>}
                    secondary={a.created_at ? new Date(a.created_at).toLocaleString() : ''}
                  />
                  <Chip label={a.source} size="small" variant="outlined" />
                </ListItem>
              ))}
            </List>
          </Box>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
}


function AgentListItem({ agent: a, onEdit, onDelete }) {
  const [expanded, setExpanded] = useState(false);
  const hasSkills = a.skills?.length > 0;
  const hasTools = a.allowed_tools?.length > 0;
  const hasInstructions = !!a.instructions;
  const hasValidation = a.tools_config?.validation;

  return (
    <ListItem divider alignItems="flex-start" sx={{ flexDirection: 'column', pr: 1 }}>
      <Box display="flex" width="100%" alignItems="center">
        <Box flex={1}>
          <Box display="flex" alignItems="center" gap={1} flexWrap="wrap">
            <Typography variant="body2" fontWeight={600}>{a.name}</Typography>
            <Chip label={a.role} size="small" variant="outlined" />
            <Chip label={`ID ${a.id}`} size="small" />
            {hasSkills && a.skills.map(s => (
              <Chip
                key={s}
                label={s}
                size="small"
                color="primary"
                variant="outlined"
                sx={{ fontSize: 10 }}
                title="Skill"
              />
            ))}
            {hasTools && a.allowed_tools.map(t => (
              <Chip
                key={t}
                label={t}
                size="small"
                color="secondary"
                variant="outlined"
                sx={{ fontSize: 10 }}
                title="Tool"
              />
            ))}
          </Box>
          {a.description && (
            <Typography variant="caption" color="text.secondary">{a.description}</Typography>
          )}
          {hasValidation && (
            <Box display="flex" gap={0.5} mt={0.5} flexWrap="wrap">
              {Object.entries(hasValidation.skills || {}).map(([sk, status]) => (
                <Chip key={sk} label={`${sk}: ${status}`} size="small"
                  color={status.includes('⚠️') ? 'warning' : 'success'} sx={{ fontSize: 9 }} />
              ))}
              {Object.entries(hasValidation.tools || {}).map(([tn, status]) => (
                <Chip key={tn} label={`${tn}: ${status}`} size="small"
                  color={status.includes('⚠️') ? 'warning' : 'success'} sx={{ fontSize: 9 }} />
              ))}
            </Box>
          )}
        </Box>
        <Box display="flex" alignItems="center" gap={0.5} ml={1}>
          {hasInstructions && (
            <Tooltip title={expanded ? 'Hide instructions' : 'Show instructions'}>
              <IconButton size="small" onClick={() => setExpanded(e => !e)}>
                <Settings fontSize="small" color={expanded ? 'primary' : 'inherit'} />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title="Edit agent"><IconButton size="small" onClick={onEdit}><Edit fontSize="small" /></IconButton></Tooltip>
          <Tooltip title="Delete agent"><IconButton size="small" color="error" onClick={onDelete}><Delete fontSize="small" /></IconButton></Tooltip>
        </Box>
      </Box>
      {expanded && hasInstructions && (
        <Box mt={1} p={1} bgcolor="#f5f5f5" borderRadius={1} width="100%">
          <Typography variant="caption" color="text.secondary" fontWeight={600}>Instructions:</Typography>
          <Typography variant="caption" component="pre" sx={{ whiteSpace: 'pre-wrap', display: 'block', mt: 0.5 }}>
            {a.instructions}
          </Typography>
        </Box>
      )}
    </ListItem>
  );
}


function OrgAgentForm({ onSubmit, onCancel, initialValue = null, submitLabel = 'Add' }) {
  const [f, setF] = useState({
    name: initialValue?.name || '',
    role: initialValue?.role || '',
    description: initialValue?.description || '',
    instructions: initialValue?.instructions || '',
    skills: Array.isArray(initialValue?.skills) ? initialValue.skills.join(', ') : (initialValue?.skills || ''),
    allowed_tools: Array.isArray(initialValue?.allowed_tools) ? initialValue.allowed_tools.join(', ') : (initialValue?.allowed_tools || ''),
  });

  const handleSubmit = () => {
    onSubmit({
      name: f.name,
      role: f.role,
      description: f.description,
      instructions: f.instructions,
      skills: f.skills.split(',').map(s => s.trim()).filter(Boolean),
      allowed_tools: f.allowed_tools.split(',').map(s => s.trim()).filter(Boolean),
    });
  };

  return (
    <Box sx={{ p: 1, mb: 2, border: '1px solid #e0e0e0', borderRadius: 1 }}>
      <Box display="flex" gap={1}>
        <TextField size="small" fullWidth label="Agent Name" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} margin="dense" required />
        <TextField size="small" fullWidth label="Role" value={f.role} onChange={e => setF({ ...f, role: e.target.value })} margin="dense" required placeholder="e.g., auditor" />
      </Box>
      <TextField size="small" fullWidth label="Description" value={f.description} onChange={e => setF({ ...f, description: e.target.value })} margin="dense" placeholder="What this agent does" />
      <TextField
        size="small" fullWidth label="Skills (comma-separated)"
        value={f.skills}
        onChange={e => setF({ ...f, skills: e.target.value })}
        margin="dense"
        placeholder="e.g., code_audit, log_analysis"
        helperText="Skill IDs this agent has access to"
      />
      <TextField
        size="small" fullWidth label="Allowed Tools (comma-separated)"
        value={f.allowed_tools}
        onChange={e => setF({ ...f, allowed_tools: e.target.value })}
        margin="dense"
        placeholder="e.g., list_tools, run_code_audit"
        helperText="Tool names this agent is permitted to use"
      />
      <TextField
        size="small" fullWidth label="Instructions"
        value={f.instructions}
        onChange={e => setF({ ...f, instructions: e.target.value })}
        margin="dense"
        multiline
        rows={4}
        placeholder="Detailed behaviour instructions for this agent (Markdown supported)"
      />
      <Box display="flex" gap={1} mt={1}>
        <Button size="small" variant="contained" onClick={handleSubmit}>{submitLabel}</Button>
        <Button size="small" onClick={onCancel}>Cancel</Button>
      </Box>
    </Box>
  );
}


function OrgTaskForm({ agents, onSubmit, onCancel, initialValue = null, submitLabel = 'Add' }) {
  const [f, setF] = useState({
    title: initialValue?.title || '',
    description: initialValue?.description || '',
    priority: initialValue?.priority || 'medium',
    status: initialValue?.status || 'pending',
    agent_id: initialValue?.agent_id || '',
  });
  return (
    <Box sx={{ p: 1, mb: 2, border: '1px solid #e0e0e0', borderRadius: 1 }}>
      <TextField size="small" fullWidth label="Task Title" value={f.title} onChange={e => setF({ ...f, title: e.target.value })} margin="dense" required />
      <TextField size="small" fullWidth label="Description" value={f.description} onChange={e => setF({ ...f, description: e.target.value })} margin="dense" />
      <Box display="flex" gap={1}>
        <TextField size="small" select fullWidth label="Priority" value={f.priority} onChange={e => setF({ ...f, priority: e.target.value })} margin="dense">
          <MenuItem value="low">Low</MenuItem>
          <MenuItem value="medium">Medium</MenuItem>
          <MenuItem value="high">High</MenuItem>
        </TextField>
        <TextField size="small" select fullWidth label="Status" value={f.status} onChange={e => setF({ ...f, status: e.target.value })} margin="dense">
          <MenuItem value="pending">Pending</MenuItem>
          <MenuItem value="in_progress">In Progress</MenuItem>
          <MenuItem value="completed">Completed</MenuItem>
        </TextField>
        <TextField size="small" select fullWidth label="Assign Agent" value={f.agent_id} onChange={e => setF({ ...f, agent_id: e.target.value ? parseInt(e.target.value) : '' })} margin="dense">
          <MenuItem value="">Unassigned</MenuItem>
          {agents.map(a => <MenuItem key={a.id} value={a.id}>{a.name}</MenuItem>)}
        </TextField>
      </Box>
      <Box display="flex" gap={1} mt={1}>
        <Button size="small" variant="contained" onClick={() => onSubmit({ ...f, agent_id: f.agent_id || null })}>{submitLabel}</Button>
        <Button size="small" onClick={onCancel}>Cancel</Button>
      </Box>
    </Box>
  );
}


// ── Tools Tab ────────────────────────────────────────────────────────

function ToolsTab({ tools, fetchAll }) {
  const [availableTools, setAvailableTools] = useState([]);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    axios.get('/api/tools/available')
      .then(r => setAvailableTools(r.data))
      .catch(() => {});
  }, []);

  const handleToggle = async (tool) => {
    try {
      await axios.patch(`/api/tools/${tool.id}`, { is_active: !tool.is_active });
      fetchAll();
    } catch (e) {
      alert('Toggle failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  const filtered = tools.filter(t =>
    t.name.toLowerCase().includes(filter.toLowerCase()) ||
    t.description?.toLowerCase().includes(filter.toLowerCase()) ||
    t.tool_type?.toLowerCase().includes(filter.toLowerCase())
  );

  const typeColor = (type) => {
    if (type === 'cli') return 'primary';
    if (type === 'mcp') return 'secondary';
    if (type === 'function') return 'success';
    return 'default';
  };

  return (
    <Box>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h6">Registered Tools</Typography>
        <IconButton onClick={fetchAll}><Refresh /></IconButton>
      </Box>

      <TextField
        size="small" fullWidth
        placeholder="Filter by name, type, or description…"
        value={filter}
        onChange={e => setFilter(e.target.value)}
        sx={{ mb: 2 }}
      />

      {filtered.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <Build sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
          <Typography color="text.secondary">No tools registered yet.</Typography>
          <Typography variant="body2" color="text.secondary" mt={1}>
            Ask Atlas to create a tool via Telegram, or use the Tool Factory agent.
          </Typography>
        </Paper>
      ) : (
        <Grid container spacing={2} sx={{ mb: 3 }}>
          {filtered.map(t => (
            <Grid item xs={12} md={6} lg={4} key={t.id}>
              <Card sx={{ opacity: t.is_active ? 1 : 0.6 }}>
                <CardContent>
                  <Box display="flex" justifyContent="space-between" alignItems="flex-start" mb={1}>
                    <Box>
                      <Typography variant="h6" sx={{ fontSize: 15, fontWeight: 600 }}>{t.name}</Typography>
                      <Box display="flex" gap={0.5} mt={0.5} flexWrap="wrap">
                        <Chip label={t.tool_type} size="small" color={typeColor(t.tool_type)} />
                        <Chip label={t.is_active ? 'active' : 'disabled'} size="small"
                          color={t.is_active ? 'success' : 'default'} variant="outlined" />
                        <Chip label={`used ${t.use_count}×`} size="small" variant="outlined" />
                      </Box>
                    </Box>
                    <Tooltip title={t.is_active ? 'Disable tool' : 'Enable tool'}>
                      <IconButton size="small" onClick={() => handleToggle(t)}
                        color={t.is_active ? 'warning' : 'success'}>
                        {t.is_active ? <PauseCircle fontSize="small" /> : <PlayCircle fontSize="small" />}
                      </IconButton>
                    </Tooltip>
                  </Box>
                  <Typography variant="body2" color="text.secondary" sx={{ minHeight: 36 }}>
                    {t.description}
                  </Typography>
                  <Typography variant="caption" color="text.disabled" display="block" mt={1}>
                    Created by: {t.created_by}
                    {t.last_used_at ? ` · last used ${new Date(t.last_used_at).toLocaleDateString()}` : ''}
                  </Typography>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      {availableTools.length > 0 && (
        <Box>
          <Typography variant="subtitle1" fontWeight={600} mb={1}>
            Available Plugins (on disk, not yet registered)
          </Typography>
          <List dense>
            {availableTools.filter(at => !tools.find(t => t.name === at.name)).map(at => (
              <ListItem key={at.name} divider>
                <ListItemText
                  primary={<Box display="flex" gap={1} alignItems="center">
                    <Typography variant="body2">{at.name}</Typography>
                    <Chip label={at.type} size="small" variant="outlined" />
                  </Box>}
                  secondary={at.description}
                />
              </ListItem>
            ))}
          </List>
        </Box>
      )}
    </Box>
  );
}


// ── Skills Tab ───────────────────────────────────────────────────────

function SkillsTab({ skills, fetchAll }) {
  const [createOpen, setCreateOpen] = useState(false);
  const [editSkill, setEditSkill] = useState(null);
  const [testSkill, setTestSkill] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [testLoading, setTestLoading] = useState(false);
  const [reloadLoading, setReloadLoading] = useState(false);
  const [filter, setFilter] = useState('');

  const handleCreate = async (data) => {
    try {
      await axios.post(`${API}/skills`, data);
      setCreateOpen(false);
      fetchAll();
    } catch (e) {
      console.error('Create skill failed', e);
      alert('Failed to create skill: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleUpdate = async (id, data) => {
    try {
      await axios.put(`${API}/skills/${id}`, data);
      setEditSkill(null);
      fetchAll();
    } catch (e) {
      console.error('Update skill failed', e);
      alert('Failed to update skill: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleDelete = async (id, name) => {
    if (!window.confirm(`Delete skill "${name}"?\n\nThis will permanently remove the SKILL.md file.`)) return;
    try {
      await axios.delete(`${API}/skills/${id}`);
      fetchAll();
    } catch (e) {
      console.error('Delete skill failed', e);
      alert('Failed to delete skill: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleReload = async () => {
    setReloadLoading(true);
    try {
      const r = await axios.post(`${API}/skills/reload`);
      alert(`Reloaded ${r.data.count} skills`);
      fetchAll();
    } catch (e) {
      alert('Reload failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setReloadLoading(false);
    }
  };

  const handleTest = async (skill, testInput) => {
    setTestLoading(true);
    setTestResult(null);
    try {
      const r = await axios.post(`${API}/skills/${skill.id}/test`, { input: testInput });
      setTestResult(r.data);
    } catch (e) {
      setTestResult({ error: e.response?.data?.detail || e.message });
    } finally {
      setTestLoading(false);
    }
  };

  const filteredSkills = skills.filter(s =>
    s.name.toLowerCase().includes(filter.toLowerCase()) ||
    s.description.toLowerCase().includes(filter.toLowerCase()) ||
    s.tags?.some(t => t.toLowerCase().includes(filter.toLowerCase()))
  );

  return (
    <Box>
      {/* Header */}
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h6">Skills Management</Typography>
        <Box display="flex" gap={1}>
          <Button
            size="small"
            startIcon={<Refresh />}
            onClick={handleReload}
            disabled={reloadLoading}
            variant="outlined"
          >
            {reloadLoading ? 'Reloading…' : 'Reload'}
          </Button>
          <Button
            size="small"
            variant="contained"
            startIcon={<Add />}
            onClick={() => setCreateOpen(true)}
          >
            Create Skill
          </Button>
        </Box>
      </Box>

      {/* Filter */}
      <TextField
        size="small"
        fullWidth
        placeholder="Filter skills by name, description, or tags..."
        value={filter}
        onChange={e => setFilter(e.target.value)}
        sx={{ mb: 2 }}
      />

      {/* Skills Grid */}
      {filteredSkills.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <School sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
          <Typography color="text.secondary">No skills found.</Typography>
          <Typography variant="body2" color="text.secondary" mt={1}>
            Create your first skill or adjust your filter.
          </Typography>
        </Paper>
      ) : (
        <Grid container spacing={2}>
          {filteredSkills.map(skill => (
            <Grid item xs={12} md={6} lg={4} key={skill.id}>
              <Card>
                <CardContent>
                  <Box display="flex" justifyContent="space-between" alignItems="flex-start" mb={1}>
                    <Typography variant="h6" noWrap sx={{ maxWidth: 200 }}>{skill.name}</Typography>
                    <Box display="flex" gap={0.5}>
                      <Tooltip title="Test skill">
                        <IconButton size="small" onClick={() => setTestSkill(skill)} color="primary">
                          <Science fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Edit skill">
                        <IconButton size="small" onClick={() => setEditSkill(skill)}>
                          <Edit fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete skill">
                        <IconButton size="small" onClick={() => handleDelete(skill.id, skill.name)} color="error">
                          <Delete fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Box>
                  </Box>

                  <Typography variant="body2" color="text.secondary" sx={{ mb: 1, minHeight: 40 }}>
                    {skill.description}
                  </Typography>

                  <Box display="flex" flexWrap="wrap" gap={0.5} mb={1}>
                    <Chip
                      label={skill.is_active ? 'Active' : 'Inactive'}
                      size="small"
                      color={skill.is_active ? 'success' : 'default'}
                    />
                    <Chip
                      label={skill.is_knowledge_only ? 'Knowledge' : 'Tools'}
                      size="small"
                      variant="outlined"
                    />
                    {skill.tags?.map(tag => (
                      <Chip key={tag} label={tag} size="small" variant="outlined" />
                    ))}
                  </Box>

                  <Typography variant="caption" color="text.secondary">
                    ID: {skill.id} • v{skill.version}
                  </Typography>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      {/* Create Skill Dialog */}
      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Create New Skill</DialogTitle>
        <DialogContent>
          <SkillForm onSubmit={handleCreate} onCancel={() => setCreateOpen(false)} />
        </DialogContent>
      </Dialog>

      {/* Edit Skill Dialog */}
      {editSkill && (
        <Dialog open onClose={() => setEditSkill(null)} maxWidth="md" fullWidth>
          <DialogTitle>Edit Skill: {editSkill.name}</DialogTitle>
          <DialogContent>
            <SkillForm
              skill={editSkill}
              onSubmit={(data) => handleUpdate(editSkill.id, data)}
              onCancel={() => setEditSkill(null)}
            />
          </DialogContent>
        </Dialog>
      )}

      {/* Test Skill Dialog */}
      {testSkill && (
        <Dialog open onClose={() => { setTestSkill(null); setTestResult(null); }} maxWidth="md" fullWidth>
          <DialogTitle>Test Skill: {testSkill.name}</DialogTitle>
          <DialogContent>
            <SkillTestPanel
              skill={testSkill}
              onTest={handleTest}
              testResult={testResult}
              testLoading={testLoading}
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => { setTestSkill(null); setTestResult(null); }}>Close</Button>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  );
}


function SkillForm({ skill, onSubmit, onCancel }) {
  const [form, setForm] = useState({
    name: skill?.name || '',
    description: skill?.description || '',
    instructions: skill?.instructions || '',
    tags: skill?.tags?.join(', ') || '',
    routing_hints: skill?.routing_hints?.join('\n') || '',
    is_active: skill?.is_active ?? true,
  });

  const handleSubmit = () => {
    onSubmit({
      ...form,
      tags: form.tags.split(',').map(t => t.trim()).filter(Boolean),
      routing_hints: form.routing_hints.split('\n').map(h => h.trim()).filter(Boolean),
    });
  };

  return (
    <Box sx={{ pt: 1 }}>
      <TextField
        fullWidth
        label="Skill Name"
        value={form.name}
        onChange={e => setForm({ ...form, name: e.target.value })}
        margin="normal"
        required
        helperText="A human-readable name for your skill"
      />
      <TextField
        fullWidth
        label="Description"
        value={form.description}
        onChange={e => setForm({ ...form, description: e.target.value })}
        margin="normal"
        required
        helperText="One-line description of what this skill does"
      />
      <TextField
        fullWidth
        label="Tags"
        value={form.tags}
        onChange={e => setForm({ ...form, tags: e.target.value })}
        margin="normal"
        helperText="Comma-separated keywords (e.g., writing, productivity, email)"
      />
      <TextField
        fullWidth
        label="Routing Hints"
        value={form.routing_hints}
        onChange={e => setForm({ ...form, routing_hints: e.target.value })}
        margin="normal"
        multiline
        rows={3}
        helperText="Natural language phrases that trigger this skill (one per line)"
      />
      <TextField
        fullWidth
        label="Instructions"
        value={form.instructions}
        onChange={e => setForm({ ...form, instructions: e.target.value })}
        margin="normal"
        multiline
        rows={10}
        required
        helperText="Detailed instructions for the AI (Markdown supported)"
      />
      <Box display="flex" gap={1} mt={2}>
        <Button variant="contained" onClick={handleSubmit}>
          {skill ? 'Update Skill' : 'Create Skill'}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </Box>
    </Box>
  );
}


function SkillTestPanel({ skill, onTest, testResult, testLoading }) {
  const [input, setInput] = useState('');

  const suggestedTests = skill.routing_hints?.slice(0, 3) || [
    `Test the ${skill.name} skill`,
    `Demonstrate ${skill.name}`,
    `Show me how ${skill.name} works`,
  ];

  return (
    <Box sx={{ pt: 1 }}>
      <Typography variant="subtitle2" gutterBottom>
        Test your skill by providing a prompt that should trigger it:
      </Typography>

      <Box display="flex" gap={1} mb={2}>
        {suggestedTests.map((test, i) => (
          <Chip
            key={i}
            label={test}
            size="small"
            onClick={() => setInput(test)}
            sx={{ cursor: 'pointer' }}
          />
        ))}
      </Box>

      <TextField
        fullWidth
        multiline
        rows={3}
        placeholder="Enter a test prompt..."
        value={input}
        onChange={e => setInput(e.target.value)}
        sx={{ mb: 2 }}
      />

      <Button
        variant="contained"
        startIcon={<Science />}
        onClick={() => onTest(skill, input)}
        disabled={!input || testLoading}
        sx={{ mb: 2 }}
      >
        {testLoading ? 'Testing...' : 'Run Test'}
      </Button>

      {testResult && (
        <Paper sx={{ p: 2, bgcolor: testResult.error ? '#ffebee' : '#f5f5f5' }}>
          <Typography variant="subtitle2" gutterBottom>
            {testResult.error ? 'Error:' : 'Result:'}
          </Typography>
          <Box
            component="pre"
            sx={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 14,
              m: 0,
            }}
          >
            {testResult.error || testResult.output || JSON.stringify(testResult, null, 2)}
          </Box>
        </Paper>
      )}
    </Box>
  );
}


// ── Scheduler Diagnostics Tab ───────────────────────────────────────

function SchedulerDiagnosticsTab() {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const checkHealth = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/scheduler/health`);
      setHealth(r.data);
    } catch (e) {
      setHealth({ status: 'error', error: e.message });
    } finally {
      setLoading(false);
    }
  };

  const testCron = async () => {
    setLoading(true);
    try {
      const r = await axios.post(`${API}/scheduler/test-cron`);
      setTestResult({ type: 'cron', ...r.data });
    } catch (e) {
      setTestResult({ type: 'cron', error: e.response?.data?.detail || e.message });
    } finally {
      setLoading(false);
    }
  };

  const testInterval = async () => {
    setLoading(true);
    try {
      const r = await axios.post(`${API}/scheduler/test-interval`);
      setTestResult({ type: 'interval', ...r.data });
    } catch (e) {
      setTestResult({ type: 'interval', error: e.response?.data?.detail || e.message });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    checkHealth();
  }, []);

  return (
    <Grid container spacing={3}>
      <Grid item xs={12} md={6}>
        <Paper sx={{ p: 3 }}>
          <Box display="flex" alignItems="center" mb={2}>
            <HealthAndSafety sx={{ mr: 1, color: health?.status === 'healthy' ? '#4caf50' : '#f44336' }} />
            <Typography variant="h6">Scheduler Health</Typography>
          </Box>

          {health ? (
            <Box>
              <Alert severity={health.status === 'healthy' ? 'success' : 'error'} sx={{ mb: 2 }}>
                {health.message}
              </Alert>

              <Typography variant="body2" gutterBottom>
                <strong>Status:</strong> {health.status}
              </Typography>
              <Typography variant="body2" gutterBottom>
                <strong>Running:</strong> {health.scheduler_running ? 'Yes' : 'No'}
              </Typography>
              <Typography variant="body2" gutterBottom>
                <strong>Active Jobs:</strong> {health.active_jobs_count}
              </Typography>

              {health.jobs?.length > 0 && (
                <Box mt={2}>
                  <Typography variant="subtitle2" gutterBottom>Recent Jobs:</Typography>
                  <List dense>
                    {health.jobs.map(job => (
                      <ListItem key={job.id} divider>
                        <ListItemText
                          primary={job.id}
                          secondary={`Next: ${job.next_fire_time || 'N/A'}`}
                        />
                      </ListItem>
                    ))}
                  </List>
                </Box>
              )}

              <Button
                variant="outlined"
                size="small"
                startIcon={<Refresh />}
                onClick={checkHealth}
                disabled={loading}
                sx={{ mt: 2 }}
              >
                Refresh
              </Button>
            </Box>
          ) : (
            <Typography color="text.secondary">Loading health status...</Typography>
          )}
        </Paper>
      </Grid>

      <Grid item xs={12} md={6}>
        <Paper sx={{ p: 3 }}>
          <Box display="flex" alignItems="center" mb={2}>
            <Timer sx={{ mr: 1 }} />
            <Typography variant="h6">Test Scheduler</Typography>
          </Box>

          <Typography variant="body2" color="text.secondary" paragraph>
            Run these tests to verify cron and interval (heartbeat) jobs work correctly.
          </Typography>

          <Box display="flex" gap={2} mb={3}>
            <Button
              variant="contained"
              onClick={testCron}
              disabled={loading}
              startIcon={<Schedule />}
            >
              Test Cron Job
            </Button>
            <Button
              variant="outlined"
              onClick={testInterval}
              disabled={loading}
              startIcon={<Timer />}
            >
              Test Heartbeat
            </Button>
          </Box>

          {testResult && (
            <Alert severity={testResult.error ? 'error' : 'info'} sx={{ mb: 2 }}>
              {testResult.error ? (
                <Typography variant="body2">Error: {testResult.error}</Typography>
              ) : (
                <Box>
                  <Typography variant="body2" fontWeight={600}>
                    {testResult.type === 'cron' ? '✅ Cron Test' : '✅ Heartbeat Test'}
                  </Typography>
                  <Typography variant="body2">
                    Job ID: {testResult.job_id}
                  </Typography>
                  {testResult.scheduled_for && (
                    <Typography variant="body2">
                      Scheduled for: {testResult.scheduled_for}
                    </Typography>
                  )}
                  {testResult.interval_seconds && (
                    <Typography variant="body2">
                      Interval: {testResult.interval_seconds}s
                    </Typography>
                  )}
                  <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                    {testResult.note}
                  </Typography>
                </Box>
              )}
            </Alert>
          )}

          <Typography variant="caption" color="text.secondary">
            Cron jobs run at specific times. Heartbeat/interval jobs run periodically.
            Both are essential for reminders and scheduled tasks.
          </Typography>
        </Paper>
      </Grid>
    </Grid>
  );
}


export default Dashboard;
