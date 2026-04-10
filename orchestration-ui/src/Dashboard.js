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
  Settings, HealthAndSafety, Timer, PlayArrow,
} from '@mui/icons-material';
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [orgDialogOpen, setOrgDialogOpen] = useState(false);
  const [selectedOrg, setSelectedOrg] = useState(null);

  const fetchAll = useCallback(async () => {
    try {
      const [sumR, costR, toolR, schedR, actR, persR, orgR, skillsR] = await Promise.all([
        axios.get(`${API}/dashboard`),
        axios.get(`${API}/costs?days=30`),
        axios.get(`${API}/tools`),
        axios.get(`${API}/schedules`),
        axios.get(`${API}/activity?limit=30`),
        axios.get(`${API}/persona`),
        axios.get(`${API}/orgs`),
        axios.get(`${API}/skills`),
      ]);
      setSummary(sumR.data);
      setCosts(costR.data);
      setTools(toolR.data);
      setSchedules(schedR.data);
      setActivity(actR.data);
      setPersona(persR.data);
      setOrgs(orgR.data);
      setSkills(skillsR.data);
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
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Overview" />
        <Tab label="Organizations" />
        <Tab label="Activity" />
        <Tab label="Skills" />
        <Tab label="System" />
      </Tabs>

      {/* Tab Content */}
      {tab === 0 && <OverviewTab costs={costs} tools={tools} schedules={schedules} persona={persona} quality={summary?.quality} fetchAll={fetchAll} />}
      {tab === 1 && <OrgsTab orgs={orgs} onCreateOrg={() => setOrgDialogOpen(true)} onSelectOrg={setSelectedOrg} fetchAll={fetchAll} />}
      {tab === 2 && <ActivityTab activity={activity} />}
      {tab === 3 && <SkillsTab skills={skills} fetchAll={fetchAll} />}
      {tab === 4 && <SchedulerDiagnosticsTab />}

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

function OverviewTab({ costs, tools, schedules, persona, quality, fetchAll }) {
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
          <Typography variant="h6" mb={1}>Quality</Typography>
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
                sx={{ mb: 2 }}
              />
              <ResponsiveContainer width="100%" height={120}>
                <LineChart data={quality.recent_scores.map((s, i) => ({ i, score: s }))}>
                  <Line type="monotone" dataKey="score" stroke="#2196f3" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </>
          ) : (
            <Typography color="text.secondary">No quality data yet</Typography>
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

function ActivityTab({ activity }) {
  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="h6" mb={2}>Recent Activity</Typography>
      {activity.length === 0 ? (
        <Typography color="text.secondary">No activity recorded yet</Typography>
      ) : (
        <List dense>
          {activity.map(a => (
            <ListItem key={a.id} divider>
              <ListItemText
                primary={
                  <Box display="flex" alignItems="center" gap={1}>
                    {a.error ? <Warning fontSize="small" color="error" /> : <CheckCircle fontSize="small" color="success" />}
                    <Typography variant="body2">{a.message_preview || `[${a.direction}]`}</Typography>
                  </Box>
                }
                secondary={
                  <Box display="flex" gap={2} mt={0.5}>
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

  const handleCompleteTask = async (taskId) => {
    await axios.post(`${API}/orgs/${org.id}/tasks/${taskId}/complete`);
    fetchOrgData();
    fetchAll();
  };

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography variant="h6">{org.name}</Typography>
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
          {agents.map(a => (
            <Chip key={a.id} label={`${a.name} (${a.role})`} sx={{ mr: 1, mb: 1 }} color="primary" variant="outlined" />
          ))}
          {agents.length === 0 && !agentForm && <Typography variant="body2" color="text.secondary">No agents yet</Typography>}
        </Box>

        {/* Tasks */}
        <Box mb={3}>
          <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
            <Typography variant="subtitle1" fontWeight={600}>Tasks ({tasks.length})</Typography>
            <Button size="small" startIcon={<Add />} onClick={() => setTaskForm(!taskForm)}>Add Task</Button>
          </Box>
          {taskForm && <OrgTaskForm agents={agents} onSubmit={handleAddTask} onCancel={() => setTaskForm(false)} />}
          <List dense>
            {tasks.map(t => (
              <ListItem key={t.id} divider secondaryAction={
                t.status !== 'completed' && (
                  <Tooltip title="Mark Complete"><IconButton size="small" onClick={() => handleCompleteTask(t.id)} color="success"><CheckCircle /></IconButton></Tooltip>
                )
              }>
                <ListItemText
                  primary={<Box display="flex" alignItems="center" gap={1}>
                    <Typography variant="body2">{t.title}</Typography>
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


function OrgAgentForm({ onSubmit, onCancel }) {
  const [f, setF] = useState({ name: '', role: '', description: '' });
  return (
    <Box sx={{ p: 1, mb: 2, border: '1px solid #e0e0e0', borderRadius: 1 }}>
      <TextField size="small" fullWidth label="Agent Name" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} margin="dense" required />
      <TextField size="small" fullWidth label="Role" value={f.role} onChange={e => setF({ ...f, role: e.target.value })} margin="dense" required placeholder="e.g., Research Analyst" />
      <TextField size="small" fullWidth label="Description" value={f.description} onChange={e => setF({ ...f, description: e.target.value })} margin="dense" />
      <Box display="flex" gap={1} mt={1}>
        <Button size="small" variant="contained" onClick={() => onSubmit(f)}>Add</Button>
        <Button size="small" onClick={onCancel}>Cancel</Button>
      </Box>
    </Box>
  );
}


function OrgTaskForm({ agents, onSubmit, onCancel }) {
  const [f, setF] = useState({ title: '', description: '', priority: 'medium', agent_id: '' });
  return (
    <Box sx={{ p: 1, mb: 2, border: '1px solid #e0e0e0', borderRadius: 1 }}>
      <TextField size="small" fullWidth label="Task Title" value={f.title} onChange={e => setF({ ...f, title: e.target.value })} margin="dense" required />
      <TextField size="small" fullWidth label="Description" value={f.description} onChange={e => setF({ ...f, description: e.target.value })} margin="dense" />
      <Box display="flex" gap={1}>
        <TextField size="small" select fullWidth label="Priority" value={f.priority} onChange={e => setF({ ...f, priority: e.target.value })} margin="dense">
          <MenuItem value="low">Low</MenuItem>
          <MenuItem value="medium">Medium</MenuItem>
          <MenuItem value="high">High</MenuItem>
          <MenuItem value="critical">Critical</MenuItem>
        </TextField>
        <TextField size="small" select fullWidth label="Assign Agent" value={f.agent_id} onChange={e => setF({ ...f, agent_id: e.target.value ? parseInt(e.target.value) : '' })} margin="dense">
          <MenuItem value="">Unassigned</MenuItem>
          {agents.map(a => <MenuItem key={a.id} value={a.id}>{a.name}</MenuItem>)}
        </TextField>
      </Box>
      <Box display="flex" gap={1} mt={1}>
        <Button size="small" variant="contained" onClick={() => onSubmit({ ...f, agent_id: f.agent_id || null })}>Add</Button>
        <Button size="small" onClick={onCancel}>Cancel</Button>
      </Box>
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
