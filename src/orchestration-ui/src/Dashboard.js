import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Responsive, WidthProvider } from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

import {

  Container, Grid, Paper, Typography, Box, Card, CardContent,

  List, ListItem, ListItemText, Chip, Button, Dialog, DialogTitle,

  DialogContent, DialogActions, TextField, MenuItem, IconButton,

  Tooltip, Tab, Tabs, Alert, LinearProgress, CircularProgress,

  ToggleButtonGroup, ToggleButton, TableContainer, Table, TableHead,

  TableBody, TableRow, TableCell, FormControl, InputLabel, Select,

  Stepper, Step, StepLabel, Checkbox, ListItemButton, ListItemIcon,

  Divider,

} from '@mui/material';

import {

  AttachMoney, Build, Schedule, TrendingUp, Psychology,

  Add, Refresh, FolderSpecial, Chat, Warning, CheckCircle,

  Delete, PauseCircle, PlayCircle, Sync, School, Edit, Science,

  Settings, HealthAndSafety, Timer, PlayArrow, AccountTree, Stop,

  BugReport, WorkHistory, Timeline, AutoAwesome, Group,

  VerifiedUser, Speed, Tune, AddCircleOutline, InfoOutlined, Close,

} from '@mui/icons-material';

import Drawer from '@mui/material/Drawer';

import { LineChart, Line, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip as RTooltip } from 'recharts';

import axios from 'axios';



const API = '/api';

const GridLayout = WidthProvider(Responsive);

const OVERVIEW_DEFAULT_LAYOUTS = {
  lg: [
    { i: 'costs',     x: 0, y: 0, w: 8, h: 5, minW: 4, minH: 3 },
    { i: 'quality',   x: 8, y: 0, w: 4, h: 5, minW: 3, minH: 3 },
    { i: 'tools',     x: 0, y: 5, w: 6, h: 4, minW: 3, minH: 2 },
    { i: 'schedules', x: 6, y: 5, w: 6, h: 4, minW: 3, minH: 2 },
    { i: 'budget',    x: 0, y: 9, w: 12, h: 4, minW: 6, minH: 3 },
    { i: 'persona',   x: 0, y: 13, w: 12, h: 4, minW: 6, minH: 3 },
  ],
  md: [
    { i: 'costs',     x: 0, y: 0, w: 6, h: 5, minW: 4, minH: 3 },
    { i: 'quality',   x: 6, y: 0, w: 4, h: 5, minW: 3, minH: 3 },
    { i: 'tools',     x: 0, y: 5, w: 5, h: 4, minW: 3, minH: 2 },
    { i: 'schedules', x: 5, y: 5, w: 5, h: 4, minW: 3, minH: 2 },
    { i: 'budget',    x: 0, y: 9, w: 10, h: 4, minW: 5, minH: 3 },
    { i: 'persona',   x: 0, y: 13, w: 10, h: 4, minW: 5, minH: 3 },
  ],
  sm: [
    { i: 'costs',     x: 0, y: 0, w: 6, h: 5, minW: 3, minH: 3 },
    { i: 'quality',   x: 0, y: 5, w: 6, h: 4, minW: 3, minH: 3 },
    { i: 'tools',     x: 0, y: 9, w: 6, h: 4, minW: 3, minH: 2 },
    { i: 'schedules', x: 0, y: 13, w: 6, h: 4, minW: 3, minH: 2 },
    { i: 'budget',    x: 0, y: 17, w: 6, h: 4, minW: 3, minH: 3 },
    { i: 'persona',   x: 0, y: 21, w: 6, h: 4, minW: 3, minH: 3 },
  ],
};



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

  const [interactionsOpen, setInteractionsOpen] = useState(false);

  const [loading, setLoading] = useState(true);

  const [error, setError] = useState(null);

  const [orgDialogOpen, setOrgDialogOpen] = useState(false);

  const [selectedOrg, setSelectedOrg] = useState(null);

  const [ownerTelegramId, setOwnerTelegramId] = useState(null);



  useEffect(() => {

    // Bootstrap auth: /api/config is public; it returns the owner's telegram
    // ID and the dashboard API key. We set BOTH as axios defaults so every
    // subsequent request carries the headers the auth middleware expects.
    // Without this, every ownership-scoped endpoint silently 401s and tabs
    // (Jobs/Repairs/Costs/etc.) display as empty.
    axios.get(`${API}/config`).then(r => {
      const tid = r.data.owner_telegram_id;
      const apiKey = r.data.dashboard_api_key;
      setOwnerTelegramId(tid);
      if (tid) {
        axios.defaults.headers.common['X-Telegram-Id'] = String(tid);
      }
      // apiKey is empty string in dev mode (no key configured) — middleware
      // allows empty, so we only set the header when there's a real value.
      if (apiKey) {
        axios.defaults.headers.common['X-API-Key'] = apiKey;
      }
    }).catch((err) => {
      // /api/config is public; if this fails, the backend is unreachable.
      // Surface it so the user sees something, instead of silently rendering
      // empty tabs.
      console.error('Failed to bootstrap dashboard config:', err);
    });

  }, []);



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

        // Tolerate empty results but surface real errors (auth/network)
        // by letting them propagate to the outer try/catch instead of
        // silently swallowing them with `() => ({data:[]})` like before.
        axios.get(`${API}/background-jobs`),

        axios.get(`${API}/repairs`),

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



  const [wizardOpen, setWizardOpen] = useState(false);



  const handleCreateOrg = async (data) => {

    try {

      await axios.post(`${API}/orgs`, data);

      setOrgDialogOpen(false);

      fetchAll();

    } catch (e) { console.error(e); }

  };



  const handleWizardComplete = () => {

    setWizardOpen(false);

    fetchAll();

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

          <SummaryCard icon={<Chat />} label="Interactions" value={summary?.interactions_today || 0} color="#2196f3" onClick={() => setInteractionsOpen(true)} />

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

        <Tab label="Agents" />

        <Tab label="Activity" />

        <Tab label="Tools" />

        <Tab label="Skills" />

        <Tab
          label="Repairs"
          icon={repairs.filter(r => !r.auto_applied && r.status === 'open').length > 0 ? <BugReport color="warning" fontSize="small" /> : undefined}
          iconPosition="end"
          title="Repair Tickets — bug reports and auto-repair pipeline. AI Agent tickets flow through debugger → programmer → QA → verify. Admin tickets pause and wait for your action."
        />

        <Tab
          label="Jobs"
          icon={bgJobs.filter(j => j.status === 'running').length > 0 ? <WorkHistory color="info" fontSize="small" /> : undefined}
          iconPosition="end"
          title={
            "Background Jobs — long-running autonomous loops (e.g. 'monitor my inbox until John replies'). Different from Tasks and Schedules:\n" +
            "• Tasks (inside an Organization) = project to-dos assigned to org agents.\n" +
            "• Schedules (time-triggered jobs via APScheduler) = 'remind me every Monday 9am'.\n" +
            "• Jobs (here) = persistent async loops with tick + completion alert."
          }
        />

        <Tab label="System" />

      </Tabs>



      {/* Tab Content */}

      {tab === 0 && <OverviewTab costs={costs} tools={tools} schedules={schedules} persona={persona} quality={summary?.quality} budget={budget} fetchAll={fetchAll} ownerTelegramId={ownerTelegramId} />}

      {tab === 1 && <OrgsTab orgs={orgs} onCreateOrg={() => setWizardOpen(true)} onSelectOrg={setSelectedOrg} fetchAll={fetchAll} />}

      {tab === 2 && <AgentsTab orgs={orgs} fetchAll={fetchAll} />}

      {tab === 3 && <ActivityTab activity={activity} onViewTrace={setTraceDrawer} />}

      {tab === 4 && <ToolsTab tools={tools} fetchAll={fetchAll} />}

      {tab === 5 && <SkillsTab skills={skills} fetchAll={fetchAll} />}

      {tab === 6 && <RepairsTab repairs={repairs} fetchAll={fetchAll} />}

      {tab === 7 && <BackgroundJobsTab jobs={bgJobs} fetchAll={fetchAll} />}

      {tab === 8 && <SchedulerDiagnosticsTab />}



      <TraceDrawer open={traceDrawer.open} steps={traceDrawer.steps} sessionKey={traceDrawer.sessionKey} noUser={traceDrawer.noUser} onClose={() => setTraceDrawer({ open: false, sessionKey: null, steps: [], noUser: false })} />



      <InteractionsDrawer open={interactionsOpen} onClose={() => setInteractionsOpen(false)} />



      {/* Organization Wizard */}

      <OrgWizardDialog open={wizardOpen} onClose={() => setWizardOpen(false)} onComplete={handleWizardComplete} />



      {/* Org Detail Dialog */}

      {selectedOrg && <OrgDetailDialog org={selectedOrg} onClose={() => setSelectedOrg(null)} fetchAll={fetchAll} ownerTelegramId={ownerTelegramId} />}

    </Container>

  );

}





// -- Summary Card ------------------------------------------------------



function SummaryCard({ icon, label, value, color, onClick }) {

  const clickable = typeof onClick === 'function';

  return (

    <Card
      sx={{
        height: '100%',
        cursor: clickable ? 'pointer' : 'default',
        transition: 'transform 120ms ease, box-shadow 120ms ease',
        '&:hover': clickable ? { transform: 'translateY(-2px)', boxShadow: 3 } : undefined,
      }}
      onClick={onClick}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={clickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') onClick(e); } : undefined}
    >

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





// -- Overview Tab ----------------------------------------------------------



function OverviewTab({ costs, tools, schedules, persona, quality, budget, fetchAll, ownerTelegramId }) {

  const [layouts, setLayouts] = useState(OVERVIEW_DEFAULT_LAYOUTS);
  const saveTimer = useRef(null);

  // Load saved layout on mount
  useEffect(() => {
    axios.get(`${API}/dashboard/layout`).then(r => {
      if (r.data.layouts) setLayouts(r.data.layouts);
    }).catch(() => {});
  }, []);

  const handleLayoutChange = useCallback((_current, allLayouts) => {
    setLayouts(allLayouts);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      axios.put(`${API}/dashboard/layout`, { layouts: allLayouts }).catch(() => {});
    }, 1200);
  }, []);

  const resetLayout = useCallback(() => {
    setLayouts(OVERVIEW_DEFAULT_LAYOUTS);
    axios.put(`${API}/dashboard/layout`, { layouts: OVERVIEW_DEFAULT_LAYOUTS }).catch(() => {});
  }, []);

  return (
    <Box>
      <Box display="flex" justifyContent="flex-end" mb={1}>
        <Tooltip title="Reset tile layout to defaults">
          <Button size="small" startIcon={<Settings />} onClick={resetLayout}>Reset Layout</Button>
        </Tooltip>
      </Box>

      <GridLayout
        className="overview-grid"
        layouts={layouts}
        breakpoints={{ lg: 1200, md: 900, sm: 600 }}
        cols={{ lg: 12, md: 10, sm: 6 }}
        rowHeight={60}
        onLayoutChange={handleLayoutChange}
        draggableHandle=".grid-drag-handle"
        isResizable
        isDraggable
        compactType="vertical"
        margin={[16, 16]}
      >
        {/* Cost Chart */}
        <div key="costs">
          <Paper sx={{ p: 2, height: '100%', overflow: 'auto' }}>
            <Typography variant="h6" mb={2} className="grid-drag-handle" sx={{ cursor: 'grab' }}>Cost Trend (30 days)</Typography>
            {costs.length > 0 ? (
              <ResponsiveContainer width="100%" height={200}>
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
        </div>

        {/* Quality */}
        <div key="quality">
          <Paper sx={{ p: 2, height: '100%', overflow: 'auto' }}>
            <Box display="flex" alignItems="center" gap={1} mb={1} className="grid-drag-handle" sx={{ cursor: 'grab' }}>
              <Typography variant="h6">Quality</Typography>
              <Tooltip title="Quality is scored 0-1 based on how accurately Atlas routes requests to the right tools and agents. Each interaction is evaluated by the reflector and averaged over the last 20 responses. Higher is better.">
                <InfoOutlined sx={{ fontSize: 16, cursor: 'help', color: 'text.secondary' }} />
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
                <Chip label={quality.trend || 'stable'} size="small" color={quality.trend === 'improving' ? 'success' : quality.trend === 'declining' ? 'error' : 'default'} sx={{ mb: 1.5 }} />
                <ResponsiveContainer width="100%" height={80}>
                  <LineChart data={quality.recent_scores.map((s, i) => ({ i, score: s }))}>
                    <Line type="monotone" dataKey="score" stroke="#2196f3" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
                <Box mt={1} sx={{ borderTop: '1px solid #eee', pt: 1 }}>
                  <Typography variant="caption" color="text.secondary" fontWeight={600}>Score guide:</Typography>
                  <Box display="flex" flexDirection="column" gap={0.3} mt={0.5}>
                    <Box display="flex" alignItems="center" gap={1}>
                      <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#4caf50', flexShrink: 0 }} />
                      <Typography variant="caption" color="text.secondary">{'>='} 0.80 — Excellent</Typography>
                    </Box>
                    <Box display="flex" alignItems="center" gap={1}>
                      <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#ff9800', flexShrink: 0 }} />
                      <Typography variant="caption" color="text.secondary">0.60–0.79 — Good</Typography>
                    </Box>
                    <Box display="flex" alignItems="center" gap={1}>
                      <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#f44336', flexShrink: 0 }} />
                      <Typography variant="caption" color="text.secondary">{'<'} 0.60 — Needs attention</Typography>
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
                      <Typography variant="caption" color="text.secondary">{'>='} 0.80 — Excellent</Typography>
                    </Box>
                    <Box display="flex" alignItems="center" gap={1}>
                      <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#ff9800', flexShrink: 0 }} />
                      <Typography variant="caption" color="text.secondary">0.60–0.79 — Good</Typography>
                    </Box>
                    <Box display="flex" alignItems="center" gap={1}>
                      <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: '#f44336', flexShrink: 0 }} />
                      <Typography variant="caption" color="text.secondary">{'<'} 0.60 — Needs attention</Typography>
                    </Box>
                  </Box>
                </Box>
              </Box>
            )}
          </Paper>
        </div>

        {/* Tools */}
        <div key="tools">
          <Paper sx={{ p: 2, height: '100%', overflow: 'auto' }}>
            <Typography variant="h6" mb={1} className="grid-drag-handle" sx={{ cursor: 'grab' }}>Registered Tools</Typography>
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
        </div>

        {/* Schedules */}
        <div key="schedules">
          <Box sx={{ height: '100%' }}>
            <SchedulesPanel schedules={schedules} fetchAll={fetchAll} />
          </Box>
        </div>

        {/* Budget */}
        <div key="budget">
          <Box sx={{ height: '100%' }}>
            {budget ? (
              <BudgetPanel budget={budget} fetchAll={fetchAll} ownerTelegramId={ownerTelegramId} />
            ) : (
              <Paper sx={{ p: 2, height: '100%' }}>
                <Typography variant="h6" className="grid-drag-handle" sx={{ cursor: 'grab' }}>Budget</Typography>
                <Typography color="text.secondary">No budget data yet</Typography>
              </Paper>
            )}
          </Box>
        </div>

        {/* Persona */}
        <div key="persona">
          <Box sx={{ height: '100%' }}>
            <PersonaPanel persona={persona} fetchAll={fetchAll} ownerTelegramId={ownerTelegramId} />
          </Box>
        </div>
      </GridLayout>
    </Box>
  );

}





// -- Budget Panel -----------------------------------------------------------



function BudgetPanel({ budget, fetchAll, ownerTelegramId }) {

  const [editing, setEditing] = useState(false);

  const [saving, setSaving] = useState(false);

  const [saveError, setSaveError] = useState(null);

  const [form, setForm] = useState({ daily_cap_usd: '', monthly_cap_usd: '' });



  const openEdit = () => {

    setForm({ daily_cap_usd: String(budget.daily_cap_usd), monthly_cap_usd: String(budget.monthly_cap_usd) });

    setSaveError(null);

    setEditing(true);

  };



  const handleSave = async () => {

    setSaving(true);

    setSaveError(null);

    try {

      await axios.put(`${API}/budget`, {

        daily_cap_usd: parseFloat(form.daily_cap_usd),

        monthly_cap_usd: parseFloat(form.monthly_cap_usd),

      }, { headers: { 'X-Telegram-Id': ownerTelegramId } });

      setEditing(false);

      fetchAll();

    } catch (e) {

      setSaveError(e.response?.data?.detail || 'Save failed');

    } finally {

      setSaving(false);

    }

  };



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

        <Box sx={{ height: '100%', width: `${Math.min(pct, 100)}%`, bgcolor: barColor(pct), borderRadius: 4, transition: 'width 0.4s ease' }} />

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

        <Tooltip title="Daily and monthly spending caps. Edit to change limits.">

          <InfoOutlined sx={{ fontSize: 16, cursor: 'help', color: 'text.secondary' }} />

        </Tooltip>

        <Box flex={1} />

        {ownerTelegramId && (

          <Button size="small" startIcon={<Edit />} onClick={openEdit} variant="outlined">Edit Caps</Button>

        )}

      </Box>



      {editing && (

        <Box sx={{ mb: 2, p: 1.5, border: '1px solid #e0e0e0', borderRadius: 1 }}>

          <Typography variant="body2" fontWeight={600} mb={1}>Update spending caps</Typography>

          <Box display="flex" gap={2}>

            <TextField

              size="small" label="Daily cap (USD)" type="number" inputProps={{ min: 0, step: 0.5 }}

              value={form.daily_cap_usd} onChange={e => setForm({ ...form, daily_cap_usd: e.target.value })}

            />

            <TextField

              size="small" label="Monthly cap (USD)" type="number" inputProps={{ min: 0, step: 1 }}

              value={form.monthly_cap_usd} onChange={e => setForm({ ...form, monthly_cap_usd: e.target.value })}

            />

            <Button size="small" variant="contained" onClick={handleSave} disabled={saving}>

              {saving ? 'Saving...' : 'Save'}

            </Button>

            <Button size="small" onClick={() => setEditing(false)}>Cancel</Button>

          </Box>

          {saveError && <Alert severity="error" sx={{ mt: 1 }}>{saveError}</Alert>}

        </Box>

      )}



      <Grid container spacing={3}>

        <Grid item xs={12} md={6}>

          <BudgetRow label="Today" spent={budget.today_usd} cap={budget.daily_cap_usd} pct={budget.daily_pct} requests={budget.request_count_today} />

        </Grid>

        <Grid item xs={12} md={6}>

          <BudgetRow label="This month" spent={budget.month_usd} cap={budget.monthly_cap_usd} pct={budget.monthly_pct} requests={budget.request_count_month} />

        </Grid>

      </Grid>

      {(budget.daily_pct >= 80 || budget.monthly_pct >= 80) && (

        <Alert severity={budget.daily_pct >= 100 || budget.monthly_pct >= 100 ? 'error' : 'warning'} sx={{ mt: 1 }}>

          {budget.daily_pct >= 100

            ? 'Daily cap reached \u2014 new requests are blocked until midnight.'

            : budget.monthly_pct >= 100

            ? 'Monthly cap reached \u2014 new requests are blocked until next month.'

            : `Approaching limit \u2014 ${budget.daily_pct >= 80 ? `daily at ${budget.daily_pct}%` : `monthly at ${budget.monthly_pct}%`}. Edit caps above if needed.`}

        </Alert>

      )}

    </Paper>

  );

}





// -- Persona Panel ----------------------------------------------------------



function PersonaPanel({ persona, fetchAll, ownerTelegramId }) {

  const [editOpen, setEditOpen] = useState(false);

  const [saving, setSaving] = useState(false);

  const [saveError, setSaveError] = useState(null);

  const [form, setForm] = useState({ assistant_name: '', ocean: {} });



  const openEdit = () => {

    setForm({

      assistant_name: persona?.assistant_name || 'Atlas',

      ocean: persona?.personality?.ocean || { openness: 0.7, conscientiousness: 0.8, extraversion: 0.6, agreeableness: 0.75, neuroticism: 0.3 },

    });

    setSaveError(null);

    setEditOpen(true);

  };



  const handleSave = async () => {

    setSaving(true);

    setSaveError(null);

    try {

      await axios.put(`${API}/persona`, {

        assistant_name: form.assistant_name,

        personality: { ocean: form.ocean },

      }, { headers: { 'X-Telegram-Id': ownerTelegramId } });

      setEditOpen(false);

      fetchAll();

    } catch (e) {

      setSaveError(e.response?.data?.detail || 'Save failed');

    } finally {

      setSaving(false);

    }

  };



  return (

    <Paper sx={{ p: 2 }}>

      <Box display="flex" alignItems="center" mb={1}>

        <Psychology sx={{ mr: 1 }} />

        <Typography variant="h6">Persona</Typography>

        <Box flex={1} />

        {ownerTelegramId && (

          <Button size="small" startIcon={<Edit />} onClick={openEdit} variant="outlined">Edit</Button>

        )}

      </Box>



      {persona ? (

        <Box>

          <Typography>

            <strong>{persona.assistant_name}</strong> v{persona.version} &mdash; {persona.interviews_completed} interviews completed

          </Typography>

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



      <Dialog open={editOpen} onClose={() => setEditOpen(false)} maxWidth="sm" fullWidth>

        <DialogTitle>Edit Persona</DialogTitle>

        <DialogContent>

          <TextField

            fullWidth size="small" label="Assistant Name" margin="dense"

            value={form.assistant_name}

            onChange={e => setForm({ ...form, assistant_name: e.target.value })}

          />

          <Typography variant="body2" fontWeight={600} mt={2} mb={1}>OCEAN Personality Traits (0.0 &ndash; 1.0)</Typography>

          {Object.entries(form.ocean).map(([trait, val]) => (

            <Box key={trait} mb={1.5}>

              <Box display="flex" justifyContent="space-between">

                <Typography variant="caption" textTransform="capitalize">{trait}</Typography>

                <Typography variant="caption">{(val * 100).toFixed(0)}%</Typography>

              </Box>

              <input

                type="range" min="0" max="1" step="0.05" value={val}

                style={{ width: '100%' }}

                onChange={e => setForm({ ...form, ocean: { ...form.ocean, [trait]: parseFloat(e.target.value) } })}

              />

            </Box>

          ))}

          {saveError && <Alert severity="error" sx={{ mt: 1 }}>{saveError}</Alert>}

        </DialogContent>

        <DialogActions>

          <Button onClick={() => setEditOpen(false)}>Cancel</Button>

          <Button variant="contained" onClick={handleSave} disabled={saving}>

            {saving ? 'Saving...' : 'Save'}

          </Button>

        </DialogActions>

      </Dialog>

    </Paper>

  );

}





// -- Schedules Panel ---------------------------------------------------



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



  const [testSnack, setTestSnack] = useState(null); // { severity, msg }

  const handleTest = async (schedule) => {

    setTestResult({ loading: true, scheduleId: schedule.id });

    setTestSnack(null);

    try {

      const r = await axios.post(`${API}/schedules/${schedule.id}/test`);

      setTestResult({ success: true, scheduleId: schedule.id, ...r.data });

      setTestSnack({ severity: 'success', msg: `✓ "${schedule.description}" ran successfully at ${new Date().toLocaleTimeString()}` });

      fetchAll();

    } catch (e) {

      const detail = e.response?.data?.detail || e.message;

      setTestResult({ error: true, scheduleId: schedule.id, message: detail });

      setTestSnack({ severity: 'error', msg: `Run failed: ${detail}` });

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

              {syncing ? 'Syncing...' : 'Sync'}

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

                  <Tooltip title={testResult?.loading && testResult?.scheduleId === s.id ? 'Running…' : 'Run now (test)'} >

                    <span>

                      <IconButton

                        size="small"

                        onClick={() => handleTest(s)}

                        color={testResult?.success && testResult?.scheduleId === s.id ? 'success' : testResult?.error && testResult?.scheduleId === s.id ? 'error' : 'primary'}

                        disabled={testResult?.loading && testResult?.scheduleId === s.id}

                      >

                        {testResult?.loading && testResult?.scheduleId === s.id

                          ? <CircularProgress size={16} />

                          : <PlayArrow fontSize="small" />}

                      </IconButton>

                    </span>

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

                    {s.last_run_at ? ` ┬╖ last: ${new Date(s.last_run_at).toLocaleString()}` : ''}

                  </Typography>

                }

              />

              {/* Inline result per row */}

              {testResult?.scheduleId === s.id && !testResult?.loading && (

                <Box mt={0.5} pl={1} pr={1}>

                  {testResult.error && (

                    <Alert severity="error" sx={{ py: 0.5, px: 1, fontSize: 11 }} onClose={() => setTestResult(null)}>

                      {testResult.message}

                    </Alert>

                  )}

                  {testResult.success && (

                    <Box>

                      <Alert severity="success" sx={{ py: 0.5, px: 1, fontSize: 11, mb: testResult.result_text ? 0.5 : 0 }}

                        onClose={() => setTestResult(null)}>

                        ✓ Ran at {new Date(testResult.executed_at || Date.now()).toLocaleTimeString()}

                        {testResult.result_text ? ' — output below' : ' (no output returned)'}

                      </Alert>

                      {testResult.result_text && (

                        <Paper variant="outlined" sx={{ p: 1.5, bgcolor: 'grey.50', maxHeight: 280, overflowY: 'auto' }}>

                          <Typography variant="caption" color="text.secondary" display="block" mb={0.5} fontWeight={600}>

                            Agent Output

                          </Typography>

                          <Typography variant="body2" component="pre"

                            sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12, fontFamily: 'inherit', m: 0 }}>

                            {testResult.result_text}

                          </Typography>

                        </Paper>

                      )}

                    </Box>

                  )}

                </Box>

              )}

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

              {testResult.error ? testResult.message : `✓ Test executed: ${testResult.message}`}

            </Alert>

          )}

        </DialogContent>

        <DialogActions>

          <Button onClick={() => { setEditingSchedule(null); setTestResult(null); }}>Cancel</Button>

          <Button onClick={handleSaveEdit} variant="contained">Save Changes</Button>

        </DialogActions>

      </Dialog>

      {/* Test result snackbar — always visible in the panel */}

      {testSnack && (

        <Alert

          severity={testSnack.severity}

          sx={{ mt: 1, fontSize: 12 }}

          onClose={() => setTestSnack(null)}

        >

          {testSnack.msg}

        </Alert>

      )}

    </Paper>

  );

}







// -- Organizations Tab -------------------------------------------------



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



  const [deleteDialog, setDeleteDialog] = useState({ open: false, org: null, preview: null, loading: false, retainAgents: [], retainTasks: [], deleting: false });

  const handleDeleteOrg = async (event, org) => {
    event.stopPropagation();
    setDeleteDialog({ open: true, org, preview: null, loading: true, retainAgents: [], retainTasks: [], deleting: false });
    try {
      const r = await axios.get(`${API}/orgs/${org.id}/delete-preview`);
      setDeleteDialog(prev => ({ ...prev, preview: r.data, loading: false }));
    } catch (e) {
      setDeleteDialog(prev => ({ ...prev, loading: false }));
      alert('Failed to load delete preview: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleDeleteConfirm = async () => {
    const { org, retainAgents, retainTasks } = deleteDialog;
    setDeleteDialog(prev => ({ ...prev, deleting: true }));
    try {
      await axios.delete(`${API}/orgs/${org.id}`, { data: { retain_agent_ids: retainAgents, retain_task_ids: retainTasks } });
      setDeleteDialog({ open: false, org: null, preview: null, loading: false, retainAgents: [], retainTasks: [], deleting: false });
      fetchAll();
    } catch (e) {
      setDeleteDialog(prev => ({ ...prev, deleting: false }));
      alert('Organization delete failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  const toggleRetainAgent = (id) => setDeleteDialog(prev => ({
    ...prev,
    retainAgents: prev.retainAgents.includes(id) ? prev.retainAgents.filter(x => x !== id) : [...prev.retainAgents, id],
  }));

  const toggleRetainTask = (id) => setDeleteDialog(prev => ({
    ...prev,
    retainTasks: prev.retainTasks.includes(id) ? prev.retainTasks.filter(x => x !== id) : [...prev.retainTasks, id],
  }));

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

                  <Box display="flex" gap={1} flexWrap="wrap">

                    <Chip label={`${org.agent_count} agents`} size="small" variant="outlined" />

                    <Chip label={`${org.task_count} tasks`} size="small" variant="outlined" />

                    <Chip label={`${org.completed_tasks} done`} size="small" variant="outlined" color="success" />

                    {org.budget_cap_usd > 0 && (

                      <Chip label={`$${org.budget_cap_usd}/mo cap`} size="small" variant="outlined" color="warning" />

                    )}

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

      {/* ── Delete Preview Dialog ── */}
      <Dialog open={deleteDialog.open} onClose={() => !deleteDialog.deleting && setDeleteDialog(prev => ({ ...prev, open: false }))} maxWidth="sm" fullWidth>
        <DialogTitle>Delete Organization — {deleteDialog.org?.name}</DialogTitle>
        <DialogContent dividers>
          {deleteDialog.loading ? (
            <Box textAlign="center" py={3}><CircularProgress /><Typography sx={{ mt: 1 }} color="text.secondary">Loading preview…</Typography></Box>
          ) : deleteDialog.preview ? (
            <Box>
              <Alert severity="warning" sx={{ mb: 2 }}>
                This will permanently delete the organization and all unchecked items below.
                Check any agents or tasks you want to <strong>retain</strong> (they will be moved to a holding org).
              </Alert>

              {deleteDialog.preview.agents.length > 0 && (
                <Box mb={2}>
                  <Typography variant="subtitle2" gutterBottom>Agents ({deleteDialog.preview.agents.length})</Typography>
                  <List dense disablePadding>
                    {deleteDialog.preview.agents.map(a => (
                      <ListItem key={a.id} disablePadding>
                        <ListItemButton dense onClick={() => toggleRetainAgent(a.id)}>
                          <ListItemIcon sx={{ minWidth: 36 }}>
                            <Checkbox edge="start" size="small" checked={deleteDialog.retainAgents.includes(a.id)} tabIndex={-1} disableRipple />
                          </ListItemIcon>
                          <ListItemText primary={a.name} secondary={a.role} />
                        </ListItemButton>
                      </ListItem>
                    ))}
                  </List>
                </Box>
              )}

              {deleteDialog.preview.tasks.length > 0 && (
                <Box mb={2}>
                  <Typography variant="subtitle2" gutterBottom>Tasks ({deleteDialog.preview.tasks.length})</Typography>
                  <List dense disablePadding>
                    {deleteDialog.preview.tasks.map(t => (
                      <ListItem key={t.id} disablePadding>
                        <ListItemButton dense onClick={() => toggleRetainTask(t.id)}>
                          <ListItemIcon sx={{ minWidth: 36 }}>
                            <Checkbox edge="start" size="small" checked={deleteDialog.retainTasks.includes(t.id)} tabIndex={-1} disableRipple />
                          </ListItemIcon>
                          <ListItemText primary={t.title} secondary={`${t.status}${t.agent_name ? ` · ${t.agent_name}` : ''}`} />
                        </ListItemButton>
                      </ListItem>
                    ))}
                  </List>
                </Box>
              )}

              {deleteDialog.preview.activity_count > 0 && (
                <Typography variant="body2" color="text.secondary">
                  {deleteDialog.preview.activity_count} activity log entries will be removed.
                </Typography>
              )}

              {deleteDialog.preview.exclusive_tools?.length > 0 && (
                <Alert severity="warning" sx={{ mt: 1 }}>
                  <strong>Tools that will be permanently deleted</strong> (only used by this org):<br />
                  {deleteDialog.preview.exclusive_tools.join(', ')}
                </Alert>
              )}

              {deleteDialog.preview.exclusive_skills?.length > 0 && (
                <Alert severity="warning" sx={{ mt: 1 }}>
                  <strong>Skills that will be permanently deleted</strong> (only used by this org):<br />
                  {deleteDialog.preview.exclusive_skills.join(', ')}
                </Alert>
              )}

              {(deleteDialog.retainAgents.length > 0 || deleteDialog.retainTasks.length > 0) && (
                <Alert severity="info" sx={{ mt: 2 }}>
                  {deleteDialog.retainAgents.length} agent(s) and {deleteDialog.retainTasks.length} task(s) will be retained.
                </Alert>
              )}
            </Box>
          ) : (
            <Typography color="text.secondary">No preview available.</Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialog(prev => ({ ...prev, open: false }))} disabled={deleteDialog.deleting}>Cancel</Button>
          <Button onClick={handleDeleteConfirm} color="error" variant="contained" disabled={deleteDialog.deleting || deleteDialog.loading || !deleteDialog.preview}>
            {deleteDialog.deleting ? 'Deleting…' : 'Delete Organization'}
          </Button>
        </DialogActions>
      </Dialog>

    </Box>

  );

}





// -- Activity Tab ------------------------------------------------------



function ActivityTab({ activity, onViewTrace }) {

  const handleViewTrace = async (a) => {

    try {

      const telegramId = a.user_telegram_id;

      if (!telegramId) {

        onViewTrace({ open: true, sessionKey: null, steps: [], noUser: true });

        return;

      }

      const sessionKey = `agent_session:${telegramId}`;

      const res = await axios.get(`${API}/traces`, { params: { session_key: sessionKey, limit: 50 } });

      onViewTrace({ open: true, sessionKey, steps: res.data });

    } catch (e) { console.error('handleViewTrace error:', e); }

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


// -- Trace Drawer ------------------------------------------------------

function TraceDrawer({ open, steps, sessionKey, noUser, onClose }) {

  return (

    <Drawer anchor="right" open={open} onClose={onClose} PaperProps={{ sx: { width: 520, p: 3 } }}>

      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>

        <Typography variant="h6">Agent Thought Trace</Typography>

        <IconButton onClick={onClose}><Stop /></IconButton>

      </Box>

      {sessionKey && <Typography variant="caption" color="text.secondary" mb={2} display="block">Session: {sessionKey}</Typography>}

      {noUser ? (

        <Typography color="text.secondary">This activity row has no associated user — trace not available (system or dashboard event).</Typography>

      ) : steps.length === 0 ? (

        <Typography color="text.secondary">No trace steps recorded for this session yet. Traces are captured when tool calls are made via Telegram.</Typography>

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

                  &rarr; {step.tool_result_preview}

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


// -- Interactions Drawer ---------------------------------------------
// Opened by clicking the "Interactions" tile on the Overview tab. Fetches
// recent audit_log rows via /api/activity and lets the user drill into any
// individual interaction's tool-call trace via /api/traces?audit_log_id=…

function InteractionsDrawer({ open, onClose }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all');
  const [expandedId, setExpandedId] = useState(null);
  const [traceCache, setTraceCache] = useState({});
  const [traceLoading, setTraceLoading] = useState(null);

  const load = useCallback(async () => {
    if (!open) return;
    setLoading(true);
    setError(null);
    try {
      const r = await axios.get(`${API}/activity?limit=200`);
      setItems(Array.isArray(r.data) ? r.data : []);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to load interactions');
    } finally {
      setLoading(false);
    }
  }, [open]);

  useEffect(() => { load(); }, [load]);

  const handleExpand = async (item) => {
    if (expandedId === item.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(item.id);
    if (!traceCache[item.id]) {
      setTraceLoading(item.id);
      try {
        const r = await axios.get(`${API}/traces`, { params: { audit_log_id: item.id, limit: 100 } });
        setTraceCache(prev => ({ ...prev, [item.id]: Array.isArray(r.data) ? r.data : [] }));
      } catch (e) {
        setTraceCache(prev => ({ ...prev, [item.id]: [] }));
      } finally {
        setTraceLoading(null);
      }
    }
  };

  const filtered = items.filter(i => {
    if (filter === 'all') return true;
    if (filter === 'inbound') return i.direction === 'inbound';
    if (filter === 'outbound') return i.direction === 'outbound';
    if (filter === 'errors') return !!i.error;
    return true;
  });

  const directionIcon = (d) => d === 'inbound' ? '→' : d === 'outbound' ? '←' : '·';
  const directionColor = (d) => d === 'inbound' ? 'primary' : d === 'outbound' ? 'secondary' : 'default';

  return (
    <Drawer anchor="right" open={open} onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', sm: 620 }, p: 3 } }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
        <Box>
          <Typography variant="h6">Interactions</Typography>
          <Typography variant="caption" color="text.secondary">
            Recent audit-log entries &middot; click a row to see the agent trace
          </Typography>
        </Box>
        <Box display="flex" gap={1}>
          <IconButton onClick={load} size="small" title="Refresh"><Refresh /></IconButton>
          <IconButton onClick={onClose} size="small" title="Close"><Stop /></IconButton>
        </Box>
      </Box>

      <ToggleButtonGroup
        value={filter} exclusive size="small" fullWidth
        onChange={(_, v) => v && setFilter(v)}
        sx={{ mb: 2 }}
      >
        <ToggleButton value="all">All ({items.length})</ToggleButton>
        <ToggleButton value="inbound">Inbound</ToggleButton>
        <ToggleButton value="outbound">Outbound</ToggleButton>
        <ToggleButton value="errors">Errors ({items.filter(i => i.error).length})</ToggleButton>
      </ToggleButtonGroup>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && <LinearProgress sx={{ mb: 2 }} />}

      {!loading && filtered.length === 0 && (
        <Typography color="text.secondary">No interactions in this view.</Typography>
      )}

      <List dense disablePadding>
        {filtered.map(item => {
          const isOpen = expandedId === item.id;
          const steps = traceCache[item.id] || [];
          return (
            <Box key={item.id} sx={{ borderBottom: '1px solid #eee', pb: 1, mb: 1 }}>
              <ListItemButton onClick={() => handleExpand(item)} sx={{ borderRadius: 1, alignItems: 'flex-start' }}>
                <Box sx={{ width: 24, mr: 1, mt: 0.5, fontSize: 14, fontWeight: 600, color: 'text.secondary' }}>
                  {directionIcon(item.direction)}
                </Box>
                <ListItemText
                  primary={
                    <Box display="flex" gap={0.5} alignItems="center" flexWrap="wrap">
                      <Chip label={item.direction} size="small" color={directionColor(item.direction)} />
                      {item.platform && <Chip label={item.platform} size="small" variant="outlined" />}
                      {item.agent_name && <Chip label={item.agent_name} size="small" color="primary" variant="outlined" />}
                      {item.model_used && <Chip label={item.model_used} size="small" variant="outlined" />}
                      {item.cost_usd != null && (
                        <Typography variant="caption" fontWeight={600} color="success.main">
                          ${item.cost_usd.toFixed(4)}
                        </Typography>
                      )}
                      {item.duration_ms != null && (
                        <Typography variant="caption" color="text.secondary">{item.duration_ms}ms</Typography>
                      )}
                      {item.error && <Chip label="error" size="small" color="error" />}
                    </Box>
                  }
                  secondary={
                    <Box>
                      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', mt: 0.5 }}>
                        {item.message_preview || <em>(no preview)</em>}
                      </Typography>
                      <Typography variant="caption" color="text.disabled">
                        {item.timestamp ? new Date(item.timestamp).toLocaleString() : ''}
                      </Typography>
                    </Box>
                  }
                />
              </ListItemButton>

              {isOpen && (
                <Box sx={{ ml: 4, mt: 1, pl: 2, borderLeft: '2px solid', borderColor: 'primary.light' }}>
                  {traceLoading === item.id && <LinearProgress sx={{ mb: 1 }} />}
                  {item.error && (
                    <Alert severity="error" sx={{ mb: 1, py: 0 }}>
                      <Typography variant="caption">{item.error}</Typography>
                    </Alert>
                  )}
                  {!traceLoading && steps.length === 0 && (
                    <Typography variant="caption" color="text.secondary">
                      No tool-call trace recorded for this turn.
                    </Typography>
                  )}
                  {steps.map((step, idx) => (
                    <Box key={step.id || idx} sx={{ mb: 1 }}>
                      <Box display="flex" gap={0.5} alignItems="center" mb={0.3}>
                        <Chip label={`#${(step.step_index ?? idx) + 1}`} size="small" sx={{ fontSize: 10, height: 18 }} />
                        {step.tool_name && <Chip label={step.tool_name} size="small" color="secondary" sx={{ fontSize: 10, height: 18 }} />}
                        {step.duration_ms != null && (
                          <Typography variant="caption" color="text.secondary">{step.duration_ms}ms</Typography>
                        )}
                      </Box>
                      {step.tool_args && (
                        <Box sx={{ bgcolor: 'grey.100', borderRadius: 1, p: 0.5, mb: 0.3, fontSize: 10, fontFamily: 'monospace', overflowX: 'auto' }}>
                          {JSON.stringify(step.tool_args, null, 2).slice(0, 300)}
                        </Box>
                      )}
                      {step.tool_result_preview && (
                        <Typography variant="caption" color="text.secondary"
                          sx={{ fontFamily: 'monospace', display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 10 }}>
                          &rarr; {step.tool_result_preview}
                        </Typography>
                      )}
                    </Box>
                  ))}
                </Box>
              )}
            </Box>
          );
        })}
      </List>
    </Drawer>
  );
}


// -- Repairs Tab -------------------------------------------------------



function RepairsTab({ repairs, fetchAll }) {

  const riskColor = (r) => r === 'low' ? 'success' : r === 'medium' ? 'warning' : 'error';

  const statusColor = (s) => s === 'deployed' ? 'success' : s === 'open' || s === 'plan_ready' ? 'warning' : s === 'verification_failed' ? 'error' : 'default';

  const [newOpen, setNewOpen] = useState(false);



  return (

    <Paper sx={{ p: 2 }}>

      <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
        <Typography variant="h6">Repair Tickets</Typography>
        <Button size="small" variant="contained" startIcon={<Add />} onClick={() => setNewOpen(true)}>
          New Ticket
        </Button>
      </Box>

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

                    {r.error_context?.assigned_to && (
                      <Chip
                        label={r.error_context.assigned_to === 'admin' ? 'Admin' : 'AI Agent'}
                        size="small"
                        color={r.error_context.assigned_to === 'admin' ? 'info' : 'secondary'}
                        variant="outlined"
                      />
                    )}

                    <Typography variant="caption" color="text.secondary">{r.created_at ? new Date(r.created_at).toLocaleString() : ''}</Typography>

                  </Box>

                }

              />

            </ListItem>

          ))}

        </List>

      )}

      <NewTicketDialog open={newOpen} onClose={() => setNewOpen(false)} onComplete={() => { setNewOpen(false); fetchAll(); }} />

    </Paper>

  );

}


// -- New Ticket Dialog (manual ticket creation) ----------------------

function NewTicketDialog({ open, onClose, onComplete }) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [priority, setPriority] = useState('medium');
  const [assignedTo, setAssignedTo] = useState('ai_agent');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const reset = () => {
    setTitle('');
    setDescription('');
    setPriority('medium');
    setAssignedTo('ai_agent');
    setLoading(false);
    setError(null);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleSubmit = async () => {
    setLoading(true);
    setError(null);
    try {
      await axios.post(`${API}/tickets`, {
        title: title.trim(),
        description: description.trim() || undefined,
        priority,
        source: 'dashboard',
        assigned_to: assignedTo,
      });
      reset();
      onComplete();
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to create ticket');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        <Box display="flex" alignItems="center" gap={1}>
          <BugReport color="primary" />
          <Typography variant="h6" fontWeight={700}>New Repair Ticket</Typography>
        </Box>
      </DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <TextField
          fullWidth autoFocus required margin="dense" size="small"
          label="Title (short summary)"
          value={title} onChange={e => setTitle(e.target.value)}
          inputProps={{ maxLength: 200 }}
          helperText={`${title.length}/200`}
        />
        <TextField
          fullWidth multiline rows={5} margin="dense" size="small"
          label="Description"
          value={description} onChange={e => setDescription(e.target.value)}
          placeholder="What's broken, what you expect, steps to reproduce, any log snippets..."
          inputProps={{ maxLength: 4000 }}
          helperText={`${description.length}/4000`}
        />
        <Box display="flex" gap={2} mt={1}>
          <FormControl size="small" fullWidth>
            <InputLabel id="priority-label">Priority</InputLabel>
            <Select labelId="priority-label" label="Priority" value={priority}
              onChange={e => setPriority(e.target.value)}>
              <MenuItem value="low">Low</MenuItem>
              <MenuItem value="medium">Medium</MenuItem>
              <MenuItem value="high">High</MenuItem>
              <MenuItem value="critical">Critical</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" fullWidth>
            <InputLabel id="assign-label">Assigned to</InputLabel>
            <Select labelId="assign-label" label="Assigned to" value={assignedTo}
              onChange={e => setAssignedTo(e.target.value)}>
              <MenuItem value="ai_agent">AI Agent (auto-repair pipeline)</MenuItem>
              <MenuItem value="admin">Admin (manual action required)</MenuItem>
            </Select>
          </FormControl>
        </Box>
        <Typography variant="caption" color="text.secondary" display="block" mt={2}>
          AI Agent tickets enter the repair pipeline (debugger → programmer → QA → verify).
          Admin tickets pause the pipeline and wait for owner action.
        </Typography>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={handleClose} disabled={loading}>Cancel</Button>
        <Button variant="contained" onClick={handleSubmit}
          disabled={loading || title.trim().length < 3}>
          {loading ? 'Creating...' : 'Create Ticket'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}





// -- Background Jobs Tab -----------------------------------------------



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

      <Box display="flex" alignItems="center" gap={1} mb={1}>
        <Typography variant="h6">Background Jobs</Typography>
        <Tooltip title={
          "Background Jobs are long-running autonomous loops (e.g. 'monitor my inbox until John replies'). They are NOT the same as:\n" +
          "• Organization Tasks — project to-dos you or an agent create inside an Organization. Seen in the Organizations tab.\n" +
          "• Scheduled Jobs — cron/interval reminders created via '/schedule' or natural language. Triggered by time."
        }>
          <InfoOutlined fontSize="small" sx={{ color: 'text.secondary', cursor: 'help' }} />
        </Tooltip>
      </Box>

      {jobs.length === 0 ? (

        <Box>

          <Typography color="text.secondary">No background jobs yet.</Typography>

          <Typography variant="caption" color="text.secondary" display="block" mt={1}>

            Say things like "Monitor my inbox and alert me when..." or "Keep watching my calendar until..." to start one.

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





// -- Org Form ----------------------------------------------------------



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





// -- Org Detail Dialog -------------------------------------------------



function OrgDetailDialog({ org, onClose, fetchAll, ownerTelegramId }) {

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



        {/* Governance */}

        <OrgGovernancePanel org={org} ownerTelegramId={ownerTelegramId} />



        {/* Spend */}

        <OrgSpendPanel org={org} />

      </DialogContent>

      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>

    </Dialog>

  );

}





function OrgGovernancePanel({ org, ownerTelegramId }) {

  const [gates, setGates] = useState([]);

  const [budgetEdit, setBudgetEdit] = useState(false);

  const [budgetVal, setBudgetVal] = useState('');

  const [loading, setLoading] = useState(false);

  const [deciding, setDeciding] = useState(null);



  const fetchGates = useCallback(async () => {

    try {

      const r = await axios.get(`${API}/orgs/${org.id}/gates`);

      setGates(r.data);

    } catch (e) { console.error(e); }

  }, [org.id]);



  useEffect(() => { fetchGates(); }, [fetchGates]);



  const handleBudgetSave = async () => {

    setLoading(true);

    try {

      await axios.patch(`${API}/orgs/${org.id}/budget`, { budget_cap_usd: parseFloat(budgetVal) || 0 }, { headers: { 'X-Telegram-Id': ownerTelegramId } });

      setBudgetEdit(false);

    } catch (e) { alert('Budget update failed: ' + (e.response?.data?.detail || e.message)); }

    finally { setLoading(false); }

  };



  const handleDecide = async (gate, decision) => {

    setDeciding(gate.id);

    try {
      await axios.post(`${API}/orgs/${org.id}/gates/${gate.id}/decide`, { decision });
      fetchGates();
    } catch (e) { alert('Decision failed: ' + (e.response?.data?.detail || e.message)); }
    finally { setDeciding(null); }
  };

  const pending = gates.filter(g => g.status === 'pending');
  const decided = gates.filter(g => g.status !== 'pending');

  return (
    <Box mt={3}>
      <Typography variant="subtitle1" fontWeight={600} mb={1}>Governance</Typography>

      {/* Budget cap */}
      <Paper variant="outlined" sx={{ p: 1.5, mb: 2 }}>
        <Box display="flex" alignItems="center" gap={1}>
          <AttachMoney fontSize="small" color="warning" />
          <Typography variant="body2" fontWeight={600}>Monthly Budget Cap</Typography>
          <Box flex={1} />
          {!budgetEdit ? (
            <>
              <Typography variant="body2">
                {org.budget_cap_usd > 0 ? `$${org.budget_cap_usd.toFixed(2)}` : 'Unlimited'}
              </Typography>
              <Button size="small" onClick={() => { setBudgetVal(org.budget_cap_usd || ''); setBudgetEdit(true); }}>Edit</Button>
            </>
          ) : (
            <>
              <TextField
                size="small" type="number" value={budgetVal}
                onChange={e => setBudgetVal(e.target.value)}
                placeholder="0 = unlimited" sx={{ width: 140 }}
                inputProps={{ min: 0, step: 1 }}
              />
              <Button size="small" variant="contained" onClick={handleBudgetSave} disabled={loading}>Save</Button>
              <Button size="small" onClick={() => setBudgetEdit(false)}>Cancel</Button>
            </>
          )}
        </Box>
      </Paper>

      {/* Pending approval gates */}
      {pending.length > 0 && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          <Typography variant="body2" fontWeight={600}>{pending.length} pending approval{pending.length !== 1 ? 's' : ''}</Typography>
        </Alert>
      )}
      {gates.length === 0 ? (
        <Typography variant="body2" color="text.secondary">No approval gates yet.</Typography>
      ) : (
        <List dense>
          {[...pending, ...decided].map(g => (
            <ListItem key={g.id} divider
              secondaryAction={
                g.status === 'pending' ? (
                  <Box display="flex" gap={0.5}>
                    <Button size="small" variant="contained" color="success" disabled={deciding === g.id}
                      onClick={() => handleDecide(g, 'approved')}>Approve</Button>
                    <Button size="small" variant="outlined" color="error" disabled={deciding === g.id}
                      onClick={() => handleDecide(g, 'rejected')}>Reject</Button>
                  </Box>
                ) : (
                  <Chip label={g.status} size="small" color={g.status === 'approved' ? 'success' : 'error'} />
                )
              }
            >
              <ListItemText
                primary={<Typography variant="body2">{g.action}</Typography>}
                secondary={
                  <Box>
                    <Typography variant="caption" color="text.secondary">
                      {g.created_at ? new Date(g.created_at).toLocaleString() : ''}
                    </Typography>
                    {g.decision_note && <Typography variant="caption" display="block" color="text.secondary">Note: {g.decision_note}</Typography>}
                  </Box>
                }
              />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
}


function OrgSpendPanel({ org }) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    axios.get(`${API}/orgs/${org.id}/spend`)
      .then(r => setReport(r.data))
      .catch(e => console.error(e))
      .finally(() => setLoading(false));
  }, [org.id]);

  if (loading) return <Box mt={3}><LinearProgress /></Box>;
  if (!report) return null;

  const hasEntries = report.entries.length > 0;

  return (
    <Box mt={3}>
      <Typography variant="subtitle1" fontWeight={600} mb={1}>Spend</Typography>
      <Paper variant="outlined" sx={{ p: 1.5 }}>
        <Box display="flex" gap={2} alignItems="center" mb={hasEntries ? 1.5 : 0}>
          <Box>
            <Typography variant="h6" color={report.over_budget ? 'error.main' : 'text.primary'}>
              ${report.total_usd.toFixed(4)}
            </Typography>
            <Typography variant="caption" color="text.secondary">Total spend</Typography>
          </Box>
          {report.budget_cap_usd > 0 && (
            <Box flex={1}>
              <Box display="flex" justifyContent="space-between" mb={0.5}>
                <Typography variant="caption">{report.pct_used.toFixed(1)}% of ${report.budget_cap_usd.toFixed(2)} cap</Typography>
                {report.over_budget && <Chip label="Over budget" size="small" color="error" />}
              </Box>
              <LinearProgress
                variant="determinate"
                value={Math.min(report.pct_used, 100)}
                color={report.over_budget ? 'error' : report.pct_used > 80 ? 'warning' : 'primary'}
                sx={{ height: 6, borderRadius: 3 }}
              />
            </Box>
          )}
        </Box>
        {hasEntries && (
          <List dense>
            {report.entries.slice(0, 8).map(e => (
              <ListItem key={e.id} divider sx={{ py: 0.25 }}>
                <ListItemText
                  primary={
                    <Box display="flex" gap={1} alignItems="center">
                      <Typography variant="caption" fontWeight={600}>${e.cost_usd.toFixed(6)}</Typography>
                      {e.model_used && <Chip label={e.model_used} size="small" variant="outlined" sx={{ fontSize: 9 }} />}
                      <Typography variant="caption" color="text.secondary" noWrap sx={{ maxWidth: 260 }}>{e.description}</Typography>
                    </Box>
                  }
                  secondary={e.recorded_at ? new Date(e.recorded_at).toLocaleString() : ''}
                />
              </ListItem>
            ))}
          </List>
        )}
        {!hasEntries && <Typography variant="body2" color="text.secondary">No spend recorded yet.</Typography>}
      </Paper>
    </Box>
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
    goal_ancestry: Array.isArray(initialValue?.goal_ancestry)
      ? initialValue.goal_ancestry.join('\n')
      : '',
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
      <TextField
        size="small" fullWidth label="Goal Ancestry (one per line)"
        value={f.goal_ancestry}
        onChange={e => setF({ ...f, goal_ancestry: e.target.value })}
        margin="dense" multiline rows={2}
        placeholder="e.g.&#10;org:job-hunt&#10;goal:outreach&#10;task:email-draft"
        helperText="Optional goal chain — trace this task back to higher-level objectives"
      />
      <Box display="flex" gap={1} mt={1}>
        <Button size="small" variant="contained" onClick={() => onSubmit({
          ...f,
          agent_id: f.agent_id || null,
          goal_ancestry: f.goal_ancestry ? f.goal_ancestry.split('\n').map(s => s.trim()).filter(Boolean) : null,
        })}>{submitLabel}</Button>
        <Button size="small" onClick={onCancel}>Cancel</Button>
      </Box>
    </Box>
  );
}


// -- Tools Tab --------------------------------------------------------

function ToolsTab({ tools, fetchAll }) {
  const [availableTools, setAvailableTools] = useState([]);
  const [filter, setFilter] = useState('');
  const [wizardOpen, setWizardOpen] = useState(false);

  useEffect(() => {
    axios.get('/api/tools/available')
      .then(r => setAvailableTools(r.data))
      .catch(() => {});
  }, []);

  const handleWizardComplete = () => {
    setWizardOpen(false);
    fetchAll();
  };

  const handleToggle = async (tool) => {
    try {
      await axios.patch(`/api/tools/${tool.id}`, { is_active: !tool.is_active });
      fetchAll();
    } catch (e) {
      alert('Toggle failed: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleDelete = async (tool) => {
    const msg = `Delete tool "${tool.name}"?\n\n` +
      `This will permanently remove the plugin directory on disk and unregister the tool.\n` +
      `Agents and orgs referencing "${tool.name}" in allowed_tools will be orphaned until reassigned.`;
    if (!window.confirm(msg)) return;
    try {
      await axios.delete(`/api/tools/${tool.id}`);
      fetchAll();
    } catch (e) {
      alert('Delete failed: ' + (e.response?.data?.detail || e.message));
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
        <Box display="flex" gap={1}>
          <Button size="small" startIcon={<Refresh />} onClick={fetchAll} variant="outlined">
            Reload
          </Button>
          <Button size="small" variant="contained" startIcon={<AutoAwesome />}
            onClick={() => setWizardOpen(true)} color="secondary">
            AI Wizard
          </Button>
        </Box>
      </Box>

      <TextField
        size="small" fullWidth
        placeholder="Filter by name, type, or description..."
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

                        <Chip label={`used ${t.use_count}├ù`} size="small" variant="outlined" />

                      </Box>

                    </Box>

                    <Box display="flex" gap={0.5}>

                      <Tooltip title={t.is_active ? 'Disable tool' : 'Enable tool'}>

                        <IconButton size="small" onClick={() => handleToggle(t)}

                          color={t.is_active ? 'warning' : 'success'}>

                          {t.is_active ? <PauseCircle fontSize="small" /> : <PlayCircle fontSize="small" />}

                        </IconButton>

                      </Tooltip>

                      <Tooltip title="Delete tool">

                        <IconButton size="small" onClick={() => handleDelete(t)} color="error">

                          <Delete fontSize="small" />

                        </IconButton>

                      </Tooltip>

                    </Box>

                  </Box>

                  <Typography variant="body2" color="text.secondary" sx={{ minHeight: 36 }}>

                    {t.description}

                  </Typography>

                  <Typography variant="caption" color="text.disabled" display="block" mt={1}>

                    Created by: {t.created_by}

                    {t.last_used_at ? ` ┬╖ last used ${new Date(t.last_used_at).toLocaleDateString()}` : ''}

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

      <ToolWizardDialog open={wizardOpen} onClose={() => setWizardOpen(false)} onComplete={handleWizardComplete} />

    </Box>

  );

}





// -- Skills Tab -------------------------------------------------------



function SkillsTab({ skills, fetchAll }) {
  const [createOpen, setCreateOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [editSkill, setEditSkill] = useState(null);
  const [editLoading, setEditLoading] = useState(false);
  const [viewSkill, setViewSkill] = useState(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [testSkill, setTestSkill] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [testLoading, setTestLoading] = useState(false);
  const [reloadLoading, setReloadLoading] = useState(false);
  const [filter, setFilter] = useState('');

  // Fetch full SkillDetail (with instructions) before opening edit dialog
  const openEdit = async (skill) => {
    setEditLoading(skill.id);
    try {
      const r = await axios.get(`${API}/skills/${skill.id}`);
      setEditSkill(r.data);
    } catch (e) {
      alert('Failed to load skill detail: ' + (e.response?.data?.detail || e.message));
    } finally {
      setEditLoading(null);
    }
  };

  // Fetch full SkillDetail before opening view drawer
  const openView = async (skill) => {
    setViewLoading(skill.id);
    try {
      const r = await axios.get(`${API}/skills/${skill.id}`);
      setViewSkill(r.data);
    } catch (e) {
      alert('Failed to load skill: ' + (e.response?.data?.detail || e.message));
    } finally {
      setViewLoading(null);
    }
  };

  const handleCreate = async (data) => {
    try {
      await axios.post(`${API}/skills`, data);
      setCreateOpen(false);
      fetchAll();
    } catch (e) {
      alert('Failed to create skill: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleUpdate = async (id, data) => {
    try {
      await axios.put(`${API}/skills/${id}`, data);
      setEditSkill(null);
      fetchAll();
    } catch (e) {
      alert('Failed to update skill: ' + (e.response?.data?.detail || e.message));
    }
  };

  const handleDelete = async (id, name) => {
    if (!window.confirm(`Delete skill "${name}"?\n\nThis will permanently remove the SKILL.md file.`)) return;
    try {
      await axios.delete(`${API}/skills/${id}`);
      fetchAll();
    } catch (e) {
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
          <Button size="small" startIcon={<Refresh />} onClick={handleReload} disabled={reloadLoading} variant="outlined">
            {reloadLoading ? 'Reloading...' : 'Reload'}
          </Button>
          <Button size="small" variant="outlined" startIcon={<AutoAwesome />} onClick={() => setWizardOpen(true)} color="secondary">
            AI Wizard
          </Button>
          <Button size="small" variant="contained" startIcon={<Add />} onClick={() => setCreateOpen(true)}>
            Create Skill
          </Button>
        </Box>
      </Box>

      {/* Filter */}
      <TextField size="small" fullWidth placeholder="Filter skills by name, description, or tags..."
        value={filter} onChange={e => setFilter(e.target.value)} sx={{ mb: 2 }} />

      {/* Skills Grid */}
      {filteredSkills.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <School sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
          <Typography color="text.secondary">No skills found.</Typography>
          <Typography variant="body2" color="text.secondary" mt={1}>
            Create your first skill or use the AI Wizard.
          </Typography>
        </Paper>
      ) : (
        <Grid container spacing={2}>
          {filteredSkills.map(skill => (
            <Grid item xs={12} md={6} lg={4} key={skill.id}>
              <Card>
                <CardContent>
                  <Box display="flex" justifyContent="space-between" alignItems="flex-start" mb={1}>
                    <Typography variant="h6" noWrap sx={{ maxWidth: 180, cursor: 'pointer' }}
                      onClick={() => openView(skill)}>{skill.name}</Typography>
                    <Box display="flex" gap={0.5}>
                      <Tooltip title="View instructions">
                        <span>
                          <IconButton size="small" onClick={() => openView(skill)} color="info"
                            disabled={viewLoading === skill.id}>
                            {viewLoading === skill.id ? <CircularProgress size={14} /> : <InfoOutlined fontSize="small" />}
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip title="Test skill">
                        <IconButton size="small" onClick={() => setTestSkill(skill)} color="primary">
                          <Science fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Edit skill">
                        <span>
                          <IconButton size="small" onClick={() => openEdit(skill)}
                            disabled={editLoading === skill.id}>
                            {editLoading === skill.id ? <CircularProgress size={14} /> : <Edit fontSize="small" />}
                          </IconButton>
                        </span>
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
                    <Chip label={skill.is_active ? 'Active' : 'Inactive'} size="small"
                      color={skill.is_active ? 'success' : 'default'} />
                    <Chip label={skill.is_knowledge_only ? 'Knowledge' : 'Tools'} size="small" variant="outlined" />
                    {skill.tags?.map(tag => (
                      <Chip key={tag} label={tag} size="small" variant="outlined" />
                    ))}
                  </Box>

                  <Typography variant="caption" color="text.secondary">
                    ID: {skill.id} &middot; v{skill.version}
                  </Typography>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      {/* AI Wizard Dialog */}
      <SkillWizardDialog open={wizardOpen} onClose={() => setWizardOpen(false)} onComplete={() => { setWizardOpen(false); fetchAll(); }} />

      {/* Create Skill Dialog */}
      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Create New Skill</DialogTitle>
        <DialogContent>
          <SkillForm onSubmit={handleCreate} onCancel={() => setCreateOpen(false)} />
        </DialogContent>
      </Dialog>

      {/* Edit Skill Dialog — editSkill is a full SkillDetail with instructions */}
      {editSkill && (
        <Dialog open onClose={() => setEditSkill(null)} maxWidth="md" fullWidth>
          <DialogTitle>Edit Skill: {editSkill.name}</DialogTitle>
          <DialogContent>
            <SkillForm skill={editSkill} onSubmit={(data) => handleUpdate(editSkill.id, data)} onCancel={() => setEditSkill(null)} />
          </DialogContent>
        </Dialog>
      )}

      {/* View Instructions Drawer */}
      <Drawer anchor="right" open={!!viewSkill} onClose={() => setViewSkill(null)}
        PaperProps={{ sx: { width: { xs: '100%', sm: 520 }, p: 3 } }}>
        {viewSkill && (
          <Box>
            <Box display="flex" alignItems="center" mb={2}>
              <School sx={{ mr: 1 }} />
              <Typography variant="h6" flex={1}>{viewSkill.name}</Typography>
              <IconButton onClick={() => setViewSkill(null)}><Close /></IconButton>
            </Box>
            <Box display="flex" gap={0.5} flexWrap="wrap" mb={2}>
              <Chip label={`v${viewSkill.version}`} size="small" />
              <Chip label={viewSkill.is_active ? 'Active' : 'Inactive'} size="small"
                color={viewSkill.is_active ? 'success' : 'default'} />
              {viewSkill.tags?.map(t => <Chip key={t} label={t} size="small" variant="outlined" />)}
            </Box>
            <Typography variant="body2" color="text.secondary" mb={2}>{viewSkill.description}</Typography>
            {viewSkill.routing_hints?.length > 0 && (
              <Box mb={2}>
                <Typography variant="caption" fontWeight={600} color="text.secondary">ROUTING HINTS</Typography>
                <Box mt={0.5} display="flex" flexDirection="column" gap={0.5}>
                  {viewSkill.routing_hints.map((h, i) => (
                    <Typography key={i} variant="caption" sx={{ fontFamily: 'monospace', bgcolor: 'action.hover', px: 1, py: 0.25, borderRadius: 0.5 }}>{h}</Typography>
                  ))}
                </Box>
              </Box>
            )}
            <Typography variant="caption" fontWeight={600} color="text.secondary">INSTRUCTIONS</Typography>
            <Box mt={1} sx={{ bgcolor: 'grey.50', borderRadius: 1, p: 2, maxHeight: '60vh', overflowY: 'auto' }}>
              <Typography component="pre" variant="body2"
                sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'monospace', fontSize: 12 }}>
                {viewSkill.instructions || '(no instructions)'}
              </Typography>
            </Box>
            <Box mt={2} display="flex" gap={1}>
              <Button size="small" variant="outlined" startIcon={<Edit />} onClick={() => { setViewSkill(null); openEdit(viewSkill); }}>
                Edit This Skill
              </Button>
              <Button size="small" onClick={() => setViewSkill(null)}>Close</Button>
            </Box>
          </Box>
        )}
      </Drawer>

      {/* Test Skill Dialog */}
      {testSkill && (
        <Dialog open onClose={() => { setTestSkill(null); setTestResult(null); }} maxWidth="md" fullWidth>
          <DialogTitle>Test Skill: {testSkill.name}</DialogTitle>
          <DialogContent>
            <SkillTestPanel skill={testSkill} onTest={handleTest} testResult={testResult} testLoading={testLoading} />
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





// -- Skill Wizard Dialog ---------------------------------------------

const SKILL_WIZARD_STEPS = ['Describe', 'Interview', 'Review', 'Done'];

function SkillWizardDialog({ open, onClose, onComplete }) {
  const [step, setStep] = useState(0);
  const [description, setDescription] = useState('');
  const [sessionId, setSessionId] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [answers, setAnswers] = useState([]);
  const [message, setMessage] = useState('');
  const [preview, setPreview] = useState(null);
  const [editedPreview, setEditedPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const reset = () => {
    setStep(0);
    setDescription('');
    setSessionId(null);
    setQuestions([]);
    setAnswers([]);
    setMessage('');
    setPreview(null);
    setEditedPreview(null);
    setLoading(false);
    setError(null);
  };

  const handleClose = () => {
    if (sessionId) {
      axios.post(`${API}/skills/wizard/cancel`, { session_id: sessionId }).catch(() => {});
    }
    reset();
    onClose();
  };

  const handleStart = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await axios.post(`${API}/skills/wizard/start`, { description });
      setSessionId(r.data.session_id);
      if (r.data.questions?.length > 0) {
        setQuestions(r.data.questions);
        setAnswers(new Array(r.data.questions.length).fill(''));
      } else {
        setMessage(r.data.message || '');
      }
      setStep(1);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to start wizard');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmitAnswers = async () => {
    setLoading(true);
    setError(null);
    try {
      const combinedAnswer = questions.map((q, i) => `Q: ${q}\nA: ${answers[i] || ''}`).join('\n\n');
      const r = await axios.post(`${API}/skills/wizard/answer`, { session_id: sessionId, answer: combinedAnswer });
      if (r.data.step === 'review' && r.data.skill_preview) {
        setPreview(r.data.skill_preview);
        setEditedPreview({ ...r.data.skill_preview });
        setStep(2);
      } else if (r.data.questions?.length > 0) {
        setQuestions(r.data.questions);
        setAnswers(new Array(r.data.questions.length).fill(''));
        setMessage('');
      } else {
        setMessage(r.data.message || '');
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to process answers');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = { session_id: sessionId };
      if (editedPreview) payload.skill_data = editedPreview;
      await axios.post(`${API}/skills/wizard/save`, payload);
      setStep(3);
      setTimeout(() => { reset(); onComplete(); }, 1500);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to save skill');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth PaperProps={{ sx: { minHeight: 500 } }}>
      <DialogTitle sx={{ pb: 0 }}>
        <Box display="flex" alignItems="center" gap={1}>
          <AutoAwesome color="secondary" />
          <Typography variant="h6" fontWeight={700}>AI Skill Creation Wizard</Typography>
        </Box>
        <Typography variant="caption" color="text.secondary">
          Describe what you need &mdash; Atlas interviews you and writes the skill.
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ pt: 1 }}>
        {step < 3 && (
          <Stepper activeStep={step} sx={{ mb: 3 }}>
            {SKILL_WIZARD_STEPS.map(label => (
              <Step key={label}><StepLabel>{label}</StepLabel></Step>
            ))}
          </Stepper>
        )}

        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {loading && <LinearProgress sx={{ mb: 2 }} />}

        {/* Step 0: Describe */}
        {step === 0 && (
          <Box>
            <Typography variant="body2" color="text.secondary" mb={2}>
              Describe what you want this skill to help with. Be as specific as you like &mdash; Atlas will ask follow-up questions.
            </Typography>
            <TextField
              fullWidth multiline rows={4} autoFocus
              label="What should this skill do?"
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="e.g. I want help writing weekly devotionals for my church newsletter. They should be warm, Bible-based, and end with a reflection question."
              helperText="Give as much context as you want — tone, audience, format, examples."
            />
          </Box>
        )}

        {/* Step 1: Interview */}
        {step === 1 && (
          <Box>
            <Typography variant="subtitle2" fontWeight={700} mb={1}>
              Atlas has a few questions to craft the best skill for you:
            </Typography>
            {message && !questions.length && (
              <Typography variant="body2" color="text.secondary" mb={2} sx={{ whiteSpace: 'pre-wrap' }}>{message}</Typography>
            )}
            {questions.map((q, i) => (
              <Box key={i} mb={2}>
                <Typography variant="body2" fontWeight={500} mb={0.5}>{i + 1}. {q}</Typography>
                <TextField
                  fullWidth multiline rows={2} size="small"
                  placeholder="Your answer..."
                  value={answers[i] || ''}
                  onChange={e => setAnswers(prev => { const a = [...prev]; a[i] = e.target.value; return a; })}
                />
              </Box>
            ))}
          </Box>
        )}

        {/* Step 2: Review & edit */}
        {step === 2 && editedPreview && (
          <Box>
            <Alert severity="success" sx={{ mb: 2 }}>Skill generated! Review and edit before saving.</Alert>
            <TextField fullWidth size="small" label="Skill Name" margin="dense"
              value={editedPreview.name || ''} onChange={e => setEditedPreview({ ...editedPreview, name: e.target.value })} />
            <TextField fullWidth size="small" label="Description" margin="dense"
              value={editedPreview.description || ''} onChange={e => setEditedPreview({ ...editedPreview, description: e.target.value })} />
            <TextField fullWidth size="small" label="Tags (comma-separated)" margin="dense"
              value={Array.isArray(editedPreview.tags) ? editedPreview.tags.join(', ') : (editedPreview.tags || '')}
              onChange={e => setEditedPreview({ ...editedPreview, tags: e.target.value.split(',').map(t => t.trim()).filter(Boolean) })} />
            <TextField fullWidth size="small" label="Routing Hints (one per line)" margin="dense" multiline rows={3}
              value={Array.isArray(editedPreview.routing_hints) ? editedPreview.routing_hints.join('\n') : (editedPreview.routing_hints || '')}
              onChange={e => setEditedPreview({ ...editedPreview, routing_hints: e.target.value.split('\n').map(h => h.trim()).filter(Boolean) })} />
            <TextField fullWidth size="small" label="Instructions (Markdown)" margin="dense" multiline rows={10}
              value={editedPreview.instructions || ''} onChange={e => setEditedPreview({ ...editedPreview, instructions: e.target.value })}
              helperText="Full markdown body — this is what the AI reads when the skill is triggered." />
          </Box>
        )}

        {/* Step 3: Done */}
        {step === 3 && (
          <Box display="flex" flexDirection="column" alignItems="center" py={4}>
            <School sx={{ fontSize: 64, color: 'success.main', mb: 2 }} />
            <Typography variant="h6" color="success.main">Skill saved!</Typography>
            <Typography variant="body2" color="text.secondary">Your skill is now active and will be used by Atlas.</Typography>
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        {step < 3 ? (
          <Button onClick={handleClose} disabled={loading}>Cancel</Button>
        ) : (
          <Button onClick={handleClose}>Close</Button>
        )}
        <Box flex={1} />
        {step === 0 && (
          <Button variant="contained" color="secondary" startIcon={<AutoAwesome />}
            onClick={handleStart} disabled={!description.trim() || loading}>
            Start Interview
          </Button>
        )}
        {step === 1 && (
          <Button variant="contained" onClick={handleSubmitAnswers}
            disabled={loading || answers.every(a => !a?.trim())}>
            {loading ? 'Generating...' : 'Submit Answers'}
          </Button>
        )}
        {step === 2 && (
          <Button variant="contained" color="success" onClick={handleSave} disabled={loading}>
            {loading ? 'Saving...' : 'Save Skill'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}


// -- Tool Wizard Dialog ----------------------------------------------

const TOOL_WIZARD_STEPS = ['Describe', 'Interview', 'Review', 'Done'];

function ToolWizardDialog({ open, onClose, onComplete }) {
  const [step, setStep] = useState(0);
  const [description, setDescription] = useState('');
  const [sessionId, setSessionId] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [answers, setAnswers] = useState([]);
  const [message, setMessage] = useState('');
  const [preview, setPreview] = useState(null);
  const [editedCode, setEditedCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const reset = () => {
    setStep(0);
    setDescription('');
    setSessionId(null);
    setQuestions([]);
    setAnswers([]);
    setMessage('');
    setPreview(null);
    setEditedCode('');
    setLoading(false);
    setError(null);
  };

  const handleClose = () => {
    if (sessionId) {
      axios.post(`${API}/tools/wizard/cancel`, { session_id: sessionId }).catch(() => {});
    }
    reset();
    onClose();
  };

  const handleStart = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await axios.post(`${API}/tools/wizard/start`, { description });
      setSessionId(r.data.session_id);
      if (r.data.questions?.length > 0) {
        setQuestions(r.data.questions);
        setAnswers(new Array(r.data.questions.length).fill(''));
      } else {
        setMessage(r.data.message || '');
      }
      setStep(1);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to start wizard');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmitAnswers = async () => {
    setLoading(true);
    setError(null);
    try {
      const combinedAnswer = questions.map((q, i) => `Q: ${q}\nA: ${answers[i] || ''}`).join('\n\n');
      const r = await axios.post(`${API}/tools/wizard/answer`, { session_id: sessionId, answer: combinedAnswer });
      if (r.data.step === 'review' && r.data.tool_preview) {
        setPreview(r.data.tool_preview);
        setEditedCode(r.data.tool_preview.code || '');
        setStep(2);
      } else if (r.data.questions?.length > 0) {
        setQuestions(r.data.questions);
        setAnswers(new Array(r.data.questions.length).fill(''));
        setMessage('');
      } else {
        setMessage(r.data.message || '');
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to process answers');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = { session_id: sessionId };
      if (editedCode && editedCode !== preview?.code) {
        payload.modified_code = editedCode;
      }
      const r = await axios.post(`${API}/tools/wizard/save`, payload);
      setMessage(r.data?.message || 'Tool saved.');
      setStep(3);
      setTimeout(() => { reset(); onComplete(); }, 1800);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to save tool');
    } finally {
      setLoading(false);
    }
  };

  const criticalViolations = (preview?.safety_violations || []).filter(v =>
    /subprocess|eval|exec|os\.system/i.test(v)
  );

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth PaperProps={{ sx: { minHeight: 500 } }}>
      <DialogTitle sx={{ pb: 0 }}>
        <Box display="flex" alignItems="center" gap={1}>
          <AutoAwesome color="secondary" />
          <Typography variant="h6" fontWeight={700}>AI Tool Creation Wizard</Typography>
        </Box>
        <Typography variant="caption" color="text.secondary">
          Describe what you need &mdash; Atlas interviews you and generates a safe CLI tool.
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ pt: 1 }}>
        {step < 3 && (
          <Stepper activeStep={step} sx={{ mb: 3 }}>
            {TOOL_WIZARD_STEPS.map(label => (
              <Step key={label}><StepLabel>{label}</StepLabel></Step>
            ))}
          </Stepper>
        )}

        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {loading && <LinearProgress sx={{ mb: 2 }} />}

        {/* Step 0: Describe */}
        {step === 0 && (
          <Box>
            <Typography variant="body2" color="text.secondary" mb={2}>
              Describe what you want this tool to do. Be specific &mdash; Atlas will ask follow-ups about inputs, outputs, and network access.
            </Typography>
            <TextField
              fullWidth multiline rows={4} autoFocus
              label="What should this tool do?"
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="e.g. Given a CSV of transactions, output a monthly summary as JSON with totals per category."
              helperText="Mention expected inputs, outputs, and whether it needs the internet."
            />
          </Box>
        )}

        {/* Step 1: Interview */}
        {step === 1 && (
          <Box>
            <Typography variant="subtitle2" fontWeight={700} mb={1}>
              Atlas has a few questions before generating the tool:
            </Typography>
            {message && !questions.length && (
              <Typography variant="body2" color="text.secondary" mb={2} sx={{ whiteSpace: 'pre-wrap' }}>{message}</Typography>
            )}
            {questions.map((q, i) => (
              <Box key={i} mb={2}>
                <Typography variant="body2" fontWeight={500} mb={0.5}>{i + 1}. {q}</Typography>
                <TextField
                  fullWidth multiline rows={2} size="small"
                  placeholder="Your answer..."
                  value={answers[i] || ''}
                  onChange={e => setAnswers(prev => { const a = [...prev]; a[i] = e.target.value; return a; })}
                />
              </Box>
            ))}
          </Box>
        )}

        {/* Step 2: Review & edit */}
        {step === 2 && preview && (
          <Box>
            <Alert severity={criticalViolations.length ? 'error' : 'success'} sx={{ mb: 2 }}>
              {criticalViolations.length
                ? 'Critical safety violations detected. Fix the code before saving.'
                : 'Tool generated! Review the code and safety report before saving.'}
            </Alert>
            <Box display="flex" flexWrap="wrap" gap={1} mb={2}>
              <Chip label={`Name: ${preview.name}`} size="small" />
              {preview.requires_network && <Chip label="requires network" size="small" color="warning" />}
              {preview.allowed_hosts && <Chip label={`hosts: ${preview.allowed_hosts}`} size="small" variant="outlined" />}
              {(preview.tags || []).map(t => (
                <Chip key={t} label={t} size="small" variant="outlined" />
              ))}
            </Box>

            <Typography variant="caption" color="text.secondary">Description</Typography>
            <Typography variant="body2" mb={2}>{preview.description}</Typography>

            <Typography variant="caption" color="text.secondary">Parameters (JSON)</Typography>
            <TextField fullWidth size="small" margin="dense" multiline rows={3}
              value={preview.parameters_json || '{}'} InputProps={{ readOnly: true, sx: { fontFamily: 'monospace', fontSize: 12 } }} />

            <Typography variant="caption" color="text.secondary" mt={2} display="block">Generated Code (editable)</Typography>
            <TextField fullWidth size="small" margin="dense" multiline rows={14}
              value={editedCode}
              onChange={e => setEditedCode(e.target.value)}
              InputProps={{ sx: { fontFamily: 'monospace', fontSize: 12 } }}
              helperText="Static analysis is re-run on save if you edit the code." />

            {(preview.safety_violations || []).length > 0 && (
              <Box mt={2}>
                <Typography variant="caption" color={criticalViolations.length ? 'error' : 'warning.main'} fontWeight={700}>
                  Safety report
                </Typography>
                <List dense>
                  {preview.safety_violations.map((v, i) => (
                    <ListItem key={i} sx={{ py: 0 }}>
                      <ListItemText primaryTypographyProps={{ variant: 'caption' }} primary={`• ${v}`} />
                    </ListItem>
                  ))}
                </List>
              </Box>
            )}
          </Box>
        )}

        {/* Step 3: Done */}
        {step === 3 && (
          <Box display="flex" flexDirection="column" alignItems="center" py={4}>
            <Build sx={{ fontSize: 64, color: 'success.main', mb: 2 }} />
            <Typography variant="h6" color="success.main">Tool saved!</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'pre-wrap', textAlign: 'center', mt: 1 }}>
              {message || 'Your tool is now registered and available to agents.'}
            </Typography>
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        {step < 3 ? (
          <Button onClick={handleClose} disabled={loading}>Cancel</Button>
        ) : (
          <Button onClick={handleClose}>Close</Button>
        )}
        <Box flex={1} />
        {step === 0 && (
          <Button variant="contained" color="secondary" startIcon={<AutoAwesome />}
            onClick={handleStart} disabled={!description.trim() || loading}>
            Start Interview
          </Button>
        )}
        {step === 1 && (
          <Button variant="contained" onClick={handleSubmitAnswers}
            disabled={loading || answers.every(a => !a?.trim())}>
            {loading ? 'Generating...' : 'Submit Answers'}
          </Button>
        )}
        {step === 2 && (
          <Button variant="contained" color="success" onClick={handleSave}
            disabled={loading || criticalViolations.length > 0}>
            {loading ? 'Saving...' : 'Save Tool'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}


// -- Agent Wizard Dialog ---------------------------------------------

const AGENT_WIZARD_STEPS = ['Describe', 'Interview', 'Review', 'Done'];

function AgentWizardDialog({ open, onClose, onComplete, orgId }) {
  const [step, setStep] = useState(0);
  const [description, setDescription] = useState('');
  const [sessionId, setSessionId] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [answers, setAnswers] = useState([]);
  const [message, setMessage] = useState('');
  const [preview, setPreview] = useState(null);
  // Editable copy of preview fields — user can tweak in the Review step.
  const [edits, setEdits] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const reset = () => {
    setStep(0);
    setDescription('');
    setSessionId(null);
    setQuestions([]);
    setAnswers([]);
    setMessage('');
    setPreview(null);
    setEdits({});
    setLoading(false);
    setError(null);
  };

  const handleClose = () => {
    if (sessionId) {
      axios.post(`${API}/agents/wizard/cancel`, { session_id: sessionId }).catch(() => {});
    }
    reset();
    onClose();
  };

  const handleStart = async () => {
    if (!orgId) {
      setError('No organization selected. Open the Agents tab from within an org first.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await axios.post(`${API}/agents/wizard/start`, { org_id: orgId, description });
      setSessionId(r.data.session_id);
      if (r.data.questions?.length > 0) {
        setQuestions(r.data.questions);
        setAnswers(new Array(r.data.questions.length).fill(''));
      } else {
        setMessage(r.data.message || '');
      }
      setStep(1);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to start wizard');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmitAnswers = async () => {
    setLoading(true);
    setError(null);
    try {
      const combinedAnswer = questions.map((q, i) => `Q: ${q}\nA: ${answers[i] || ''}`).join('\n\n');
      const r = await axios.post(`${API}/agents/wizard/answer`, { session_id: sessionId, answer: combinedAnswer });
      if (r.data.step === 'review' && r.data.agent_preview) {
        setPreview(r.data.agent_preview);
        // Initialize edits from preview so the Review step is editable
        setEdits({
          name: r.data.agent_preview.name || '',
          role: r.data.agent_preview.role || '',
          description: r.data.agent_preview.description || '',
          instructions: r.data.agent_preview.instructions || '',
          skills: (r.data.agent_preview.skills || []).join(', '),
          allowed_tools: (r.data.agent_preview.allowed_tools || []).join(', '),
          model_tier: r.data.agent_preview.model_tier || 'general',
        });
        setStep(2);
      } else if (r.data.questions?.length > 0) {
        setQuestions(r.data.questions);
        setAnswers(new Array(r.data.questions.length).fill(''));
        setMessage('');
      } else {
        setMessage(r.data.message || '');
      }
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to process answers');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setLoading(true);
    setError(null);
    try {
      // Convert comma-separated skill / tool strings back to lists for the API.
      const overrides = {
        name: edits.name?.trim(),
        role: edits.role?.trim(),
        description: edits.description?.trim() || null,
        instructions: edits.instructions?.trim() || null,
        skills: edits.skills
          ? edits.skills.split(',').map(s => s.trim()).filter(Boolean)
          : [],
        allowed_tools: edits.allowed_tools
          ? edits.allowed_tools.split(',').map(s => s.trim()).filter(Boolean)
          : [],
        model_tier: edits.model_tier || 'general',
      };
      const r = await axios.post(`${API}/agents/wizard/save`, {
        session_id: sessionId,
        overrides,
      });
      setMessage(r.data?.agent?.name
        ? `Agent "${r.data.agent.name}" created.`
        : 'Agent saved.');
      setStep(3);
      setTimeout(() => { reset(); onComplete(); }, 1800);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to save agent');
    } finally {
      setLoading(false);
    }
  };

  const updateEdit = (key) => (e) => setEdits(prev => ({ ...prev, [key]: e.target.value }));

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth PaperProps={{ sx: { minHeight: 520 } }}>
      <DialogTitle sx={{ pb: 0 }}>
        <Box display="flex" alignItems="center" gap={1}>
          <AutoAwesome color="secondary" />
          <Typography variant="h6" fontWeight={700}>AI Agent Creation Wizard</Typography>
        </Box>
        <Typography variant="caption" color="text.secondary">
          Describe the role you want filled — Atlas interviews you and drafts an agent. You can edit before saving.
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ pt: 1 }}>
        {step < 3 && (
          <Stepper activeStep={step} sx={{ mb: 3 }}>
            {AGENT_WIZARD_STEPS.map(label => (
              <Step key={label}><StepLabel>{label}</StepLabel></Step>
            ))}
          </Stepper>
        )}

        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {loading && <LinearProgress sx={{ mb: 2 }} />}

        {/* Step 0: Describe */}
        {step === 0 && (
          <Box>
            <Typography variant="body2" color="text.secondary" mb={2}>
              Describe the role this agent should fill. Atlas will ask follow-ups about
              responsibilities, skills, tools, and model tier.
            </Typography>
            <TextField
              fullWidth multiline rows={4} autoFocus
              label="What should this agent do?"
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="e.g. Triage incoming Gmail every morning — label, draft replies for the top 3, snooze the rest."
              helperText="Be concrete: what tasks, what tools, how often."
            />
          </Box>
        )}

        {/* Step 1: Interview */}
        {step === 1 && (
          <Box>
            <Typography variant="subtitle2" fontWeight={700} mb={1}>
              Atlas has a few questions before drafting the agent:
            </Typography>
            {message && !questions.length && (
              <Typography variant="body2" color="text.secondary" mb={2} sx={{ whiteSpace: 'pre-wrap' }}>{message}</Typography>
            )}
            {questions.map((q, i) => (
              <Box key={i} mb={2}>
                <Typography variant="body2" fontWeight={500} mb={0.5}>{i + 1}. {q}</Typography>
                <TextField
                  fullWidth multiline rows={2} size="small"
                  placeholder="Your answer..."
                  value={answers[i] || ''}
                  onChange={e => setAnswers(prev => { const a = [...prev]; a[i] = e.target.value; return a; })}
                />
              </Box>
            ))}
          </Box>
        )}

        {/* Step 2: Review & edit */}
        {step === 2 && preview && (
          <Box>
            <Alert severity="success" sx={{ mb: 2 }}>
              Draft generated. Review and tweak any field before saving — the AI's first guess is rarely the final form.
            </Alert>

            <Box display="flex" flexWrap="wrap" gap={1} mb={2}>
              <Chip label={`Model tier: ${edits.model_tier}`} size="small" color={edits.model_tier === 'reasoning' ? 'warning' : 'default'} />
              <Chip label={`Skills: ${(edits.skills?.split(',').filter(s => s.trim()).length) || 0}`} size="small" variant="outlined" />
              <Chip label={`Tools: ${(edits.allowed_tools?.split(',').filter(s => s.trim()).length) || 0}`} size="small" variant="outlined" />
            </Box>

            <TextField fullWidth size="small" margin="dense" label="Name (snake_case)"
              value={edits.name || ''} onChange={updateEdit('name')}
              helperText="Internal identifier; lowercase, no spaces." />

            <TextField fullWidth size="small" margin="dense" label="Role"
              value={edits.role || ''} onChange={updateEdit('role')}
              helperText="Concise role title (e.g., 'inbox triage specialist')." />

            <TextField fullWidth size="small" margin="dense" label="Description"
              value={edits.description || ''} onChange={updateEdit('description')}
              multiline rows={2} />

            <TextField fullWidth size="small" margin="dense" label="Instructions (system prompt)"
              value={edits.instructions || ''} onChange={updateEdit('instructions')}
              multiline rows={5}
              helperText="Goes into the runtime system prompt; keep it actionable." />

            <TextField fullWidth size="small" margin="dense" label="Skills (comma-separated)"
              value={edits.skills || ''} onChange={updateEdit('skills')}
              helperText="Skill identifiers this agent can invoke." />

            <TextField fullWidth size="small" margin="dense" label="Allowed tools (comma-separated)"
              value={edits.allowed_tools || ''} onChange={updateEdit('allowed_tools')}
              helperText="Tool identifiers this agent has access to." />

            <TextField fullWidth size="small" margin="dense" label="Model tier"
              value={edits.model_tier || 'general'} onChange={updateEdit('model_tier')}
              select SelectProps={{ native: true }}
              helperText="fast = mini-class, general = full-size, reasoning = o1-class">
              <option value="fast">fast</option>
              <option value="general">general</option>
              <option value="reasoning">reasoning</option>
            </TextField>
          </Box>
        )}

        {/* Step 3: Done */}
        {step === 3 && (
          <Box display="flex" flexDirection="column" alignItems="center" py={4}>
            <AutoAwesome sx={{ fontSize: 64, color: 'success.main', mb: 2 }} />
            <Typography variant="h6" color="success.main">Agent saved!</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'pre-wrap', textAlign: 'center', mt: 1 }}>
              {message || 'Your agent is now registered with this organization.'}
            </Typography>
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        {step < 3 ? (
          <Button onClick={handleClose} disabled={loading}>Cancel</Button>
        ) : (
          <Button onClick={handleClose}>Close</Button>
        )}
        <Box flex={1} />
        {step === 0 && (
          <Button variant="contained" color="secondary" startIcon={<AutoAwesome />}
            onClick={handleStart} disabled={!description.trim() || loading}>
            Start Interview
          </Button>
        )}
        {step === 1 && (
          <Button variant="contained" onClick={handleSubmitAnswers}
            disabled={loading || answers.every(a => !a?.trim())}>
            {loading ? 'Drafting...' : 'Submit Answers'}
          </Button>
        )}
        {step === 2 && (
          <Button variant="contained" color="success" onClick={handleSave}
            disabled={loading || !edits.name?.trim() || !edits.role?.trim()}>
            {loading ? 'Saving...' : 'Save Agent'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}


// -- Scheduler Diagnostics Tab ---------------------------------------



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

                    {testResult.type === 'cron' ? '✓ Cron Test' : '✓ Heartbeat Test'}

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





// ── Agents Tab ─────────────────────────────────────────────────────────



function AgentsTab({ orgs, fetchAll }) {

  const [systemAgents, setSystemAgents] = useState([]);

  const [orgAgents, setOrgAgents] = useState([]);

  const [loading, setLoading] = useState(false);

  const [activeSection, setActiveSection] = useState('org');

  const [selectedAgent, setSelectedAgent] = useState(null);

  const [editDialogOpen, setEditDialogOpen] = useState(false);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const [agentToDelete, setAgentToDelete] = useState(null);

  const [deleteLoading, setDeleteLoading] = useState(false);

  // AI Wizard state — clones the Tools/Skills wizard pattern.
  // wizardOrgId is the org the new agent will belong to. We default to the
  // first non-archived org when the user opens the wizard, but they can
  // switch via the dropdown inside the wizard launcher button.
  const [wizardOpen, setWizardOpen] = useState(false);

  const [wizardOrgId, setWizardOrgId] = useState(null);

  const eligibleOrgs = (orgs || []).filter(o => o.status !== 'archived');



  const fetchAgents = async () => {

    setLoading(true);

    try {

      const [systemRes, orgRes] = await Promise.all([

        axios.get(`${API}/agents/system`),

        axios.get(`${API}/agents/org`),

      ]);

      setSystemAgents(systemRes.data);

      setOrgAgents(orgRes.data);

    } catch (e) {

      console.error('Failed to fetch agents:', e);

    } finally {

      setLoading(false);

    }

  };



  useEffect(() => { fetchAgents(); }, []);



  const handleEdit = (agent) => { setSelectedAgent(agent); setEditDialogOpen(true); };

  // Two-phase delete: open the preview dialog first; the dialog itself
  // fetches the impact preview and only confirms when the user types the
  // agent name and clicks Delete.
  const handleDeleteClick = (agent) => { setAgentToDelete(agent); setDeleteDialogOpen(true); };



  const handleDeleteConfirm = async () => {

    if (!agentToDelete) return;

    setDeleteLoading(true);

    try {

      await axios.delete(`${API}/orgs/${agentToDelete.org_id}/agents/${agentToDelete.id}`);

      setDeleteDialogOpen(false);

      setAgentToDelete(null);

      fetchAgents();

      fetchAll();

    } catch (e) {

      alert(e.response?.data?.detail || 'Failed to delete agent');

    } finally {

      setDeleteLoading(false);

    }

  };



  return (

    <Box>

      <Paper sx={{ p: 2, mb: 2 }}>

        <Box display="flex" gap={2} alignItems="center" flexWrap="wrap">

          <ToggleButtonGroup value={activeSection} exclusive onChange={(_, v) => v && setActiveSection(v)} sx={{ flex: 1, minWidth: 280 }}>

            <ToggleButton value="org">My Agents ({orgAgents.length})</ToggleButton>

            <ToggleButton value="system">System Agents ({systemAgents.length})</ToggleButton>

          </ToggleButtonGroup>

          {activeSection === 'org' && (

            <Button

              variant="contained"

              color="secondary"

              startIcon={<AutoAwesome />}

              disabled={eligibleOrgs.length === 0}

              onClick={() => {

                // Default to first eligible org; user changes via the dropdown if needed
                setWizardOrgId(prev => prev || eligibleOrgs[0]?.id || null);

                setWizardOpen(true);

              }}

            >

              AI Wizard

            </Button>

          )}

        </Box>

        {activeSection === 'org' && eligibleOrgs.length === 0 && (

          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>

            Create an organization first — agents are scoped to an org.

          </Typography>

        )}

      </Paper>



      {loading && <LinearProgress sx={{ mb: 2 }} />}



      {activeSection === 'org' && (

        <AgentsOrgSection agents={orgAgents} onEdit={handleEdit} onDelete={handleDeleteClick} loading={loading} />

      )}

      {activeSection === 'system' && (

        <AgentsSystemSection agents={systemAgents} loading={loading} />

      )}



      <Dialog open={editDialogOpen} onClose={() => setEditDialogOpen(false)} maxWidth="sm" fullWidth>

        <DialogTitle>Edit Agent</DialogTitle>

        <DialogContent>

          {selectedAgent && (

            <OrgAgentForm

              initialValue={selectedAgent}

              submitLabel="Save"

              onSubmit={async (data) => {

                try {

                  await axios.patch(`${API}/orgs/${selectedAgent.org_id}/agents/${selectedAgent.id}`, data);

                  setEditDialogOpen(false);

                  setSelectedAgent(null);

                  fetchAgents();

                  fetchAll();

                } catch (e) {

                  alert(e.response?.data?.detail || 'Failed to update agent');

                }

              }}

              onCancel={() => setEditDialogOpen(false)}

            />

          )}

        </DialogContent>

      </Dialog>



      <AgentDeletePreviewDialog

        open={deleteDialogOpen}

        agent={agentToDelete}

        onClose={() => { setDeleteDialogOpen(false); setAgentToDelete(null); }}

        onDeleted={() => { setDeleteDialogOpen(false); setAgentToDelete(null); fetchAgents(); fetchAll(); }}

      />

      {/* Pick which org the new agent goes into when more than one exists */}
      {wizardOpen && eligibleOrgs.length > 1 && (

        <Dialog open={!wizardOrgId} onClose={() => setWizardOpen(false)} maxWidth="xs" fullWidth>

          <DialogTitle>Pick organization</DialogTitle>

          <DialogContent>

            <Typography variant="body2" color="text.secondary" mb={2}>

              Which organization should the new agent belong to?

            </Typography>

            <List dense>

              {eligibleOrgs.map(o => (

                <ListItem key={o.id} button onClick={() => setWizardOrgId(o.id)}>

                  <ListItemText primary={o.name} secondary={`status: ${o.status}`} />

                </ListItem>

              ))}

            </List>

          </DialogContent>

          <DialogActions>

            <Button onClick={() => setWizardOpen(false)}>Cancel</Button>

          </DialogActions>

        </Dialog>

      )}

      <AgentWizardDialog

        open={wizardOpen && !!wizardOrgId}

        orgId={wizardOrgId}

        onClose={() => { setWizardOpen(false); setWizardOrgId(null); }}

        onComplete={() => { setWizardOpen(false); setWizardOrgId(null); fetchAgents(); fetchAll(); }}

      />

    </Box>

  );

}



// Two-phase agent delete: fetches the impact preview from the backend,
// shows tasks/activity that will be affected, requires the user to type
// the agent name to confirm. Mirrors the org-delete-preview pattern.
function AgentDeletePreviewDialog({ open, agent, onClose, onDeleted }) {

  const [preview, setPreview] = useState(null);

  const [loading, setLoading] = useState(false);

  const [confirmText, setConfirmText] = useState('');

  const [error, setError] = useState(null);

  useEffect(() => {

    if (!open || !agent) {

      setPreview(null);

      setConfirmText('');

      setError(null);

      return;

    }

    setLoading(true);

    setError(null);

    axios.get(`${API}/orgs/${agent.org_id}/agents/${agent.id}/delete-preview`)

      .then(r => setPreview(r.data))

      .catch(e => setError(e.response?.data?.detail || 'Failed to load delete preview'))

      .finally(() => setLoading(false));

  }, [open, agent?.id, agent?.org_id]);

  const handleDelete = async () => {

    if (!agent) return;

    setLoading(true);

    setError(null);

    try {

      await axios.delete(`${API}/orgs/${agent.org_id}/agents/${agent.id}`);

      onDeleted();

    } catch (e) {

      setError(e.response?.data?.detail || 'Failed to delete agent');

    } finally {

      setLoading(false);

    }

  };

  const nameMatches = preview && confirmText.trim() === preview.agent.name;

  const blocked = preview?.deletion_blocked;

  return (

    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>

      <DialogTitle>Delete agent</DialogTitle>

      <DialogContent>

        {loading && !preview && <LinearProgress sx={{ mb: 2 }} />}

        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

        {preview && (

          <Box>

            <Typography mb={1}>

              You're about to delete <strong>{preview.agent.name}</strong> ({preview.agent.role}).

            </Typography>

            {blocked && (

              <Alert severity="warning" sx={{ mb: 2 }}>

                This agent is attached to an <strong>active</strong> organization. Pause or archive the org first; the delete is blocked until then.

              </Alert>

            )}

            <Box mb={2}>

              <Typography variant="caption" color="text.secondary">Impact</Typography>

              <List dense>

                <ListItem sx={{ py: 0 }}>

                  <ListItemText

                    primary={`${preview.active_tasks_count} active task(s) — orphaned (FK SET NULL, not lost)`}

                    secondary={preview.active_tasks_count > 0

                      ? preview.active_tasks.slice(0, 5).map(t => `#${t.id} ${t.title}`).join(' · ')

                      : 'none'}

                    primaryTypographyProps={{ variant: 'body2' }}

                    secondaryTypographyProps={{ variant: 'caption' }}

                  />

                </ListItem>

                <ListItem sx={{ py: 0 }}>

                  <ListItemText

                    primary={`${preview.completed_tasks_count} completed task(s) — preserved (audit trail)`}

                    primaryTypographyProps={{ variant: 'body2' }}

                  />

                </ListItem>

                <ListItem sx={{ py: 0 }}>

                  <ListItemText

                    primary={`${preview.activity_count} activity log entr${preview.activity_count === 1 ? 'y' : 'ies'} — preserved`}

                    primaryTypographyProps={{ variant: 'body2' }}

                  />

                </ListItem>

              </List>

            </Box>

            <Typography variant="body2" color="text.secondary" mb={1}>

              To confirm, type the agent name <code>{preview.agent.name}</code> below:

            </Typography>

            <TextField

              fullWidth size="small" autoFocus

              value={confirmText}

              onChange={e => setConfirmText(e.target.value)}

              placeholder={preview.agent.name}

              disabled={blocked}

            />

          </Box>

        )}

      </DialogContent>

      <DialogActions>

        <Button onClick={onClose}>Cancel</Button>

        <Button

          onClick={handleDelete}

          color="error"

          variant="contained"

          disabled={loading || blocked || !nameMatches}

        >

          {loading ? 'Deleting...' : 'Delete agent'}

        </Button>

      </DialogActions>

    </Dialog>

  );

}



function AgentsOrgSection({ agents, onEdit, onDelete, loading }) {

  if (loading) return <Paper sx={{ p: 3, textAlign: 'center' }}><CircularProgress /><Typography sx={{ mt: 2 }} color="text.secondary">Loading agents...</Typography></Paper>;

  if (agents.length === 0) return <Paper sx={{ p: 3 }}><Alert severity="info">No custom agents yet. Create agents within your organizations to see them here.</Alert></Paper>;

  return (

    <Paper sx={{ p: 2 }}>

      <Typography variant="h6" mb={1}>Organization Agents</Typography>

      <Typography variant="body2" color="text.secondary" mb={2}>Custom agents you've created. Agents attached to active organizations cannot be deleted.</Typography>

      <TableContainer>

        <Table size="small">

          <TableHead>

            <TableRow>

              <TableCell>Name</TableCell>

              <TableCell>Role</TableCell>

              <TableCell>Organization</TableCell>

              <TableCell>Status</TableCell>

              <TableCell>Skills / Tools</TableCell>

              <TableCell align="right">Actions</TableCell>

            </TableRow>

          </TableHead>

          <TableBody>

            {agents.map(agent => (

              <TableRow key={agent.id}>

                <TableCell>{agent.name}</TableCell>

                <TableCell><Chip label={agent.role} size="small" variant="outlined" /></TableCell>

                <TableCell>

                  <Chip label={agent.org_name} color={agent.org_status === 'active' ? 'success' : 'default'} size="small" />

                </TableCell>

                <TableCell><Chip label={agent.status} size="small" color={agent.status === 'active' ? 'success' : 'default'} /></TableCell>

                <TableCell>
                  <Box display="flex" flexWrap="wrap" gap={0.5}>
                    {(agent.skills || []).map(s => (
                      <Chip key={`sk-${s}`} label={s} size="small" color="primary" variant="outlined" sx={{ fontSize: 10 }} />
                    ))}
                    {(agent.allowed_tools || []).map(t => (
                      <Chip key={`tl-${t}`} label={t} size="small" color="secondary" variant="outlined" sx={{ fontSize: 10 }} />
                    ))}
                    {!(agent.skills?.length) && !(agent.allowed_tools?.length) && (
                      <Typography variant="caption" color="text.disabled">None</Typography>
                    )}
                  </Box>
                </TableCell>

                <TableCell align="right">

                  <Tooltip title="Edit agent"><IconButton size="small" onClick={() => onEdit(agent)}><Edit fontSize="small" /></IconButton></Tooltip>

                  <Tooltip title={agent.can_delete ? 'Delete agent' : `Cannot delete: ${agent.delete_reason}`}>

                    <span>

                      <IconButton size="small" onClick={() => onDelete(agent)} disabled={!agent.can_delete} color="error">

                        <Delete fontSize="small" />

                      </IconButton>

                    </span>

                  </Tooltip>

                </TableCell>

              </TableRow>

            ))}

          </TableBody>

        </Table>

      </TableContainer>

    </Paper>

  );

}



function AgentsSystemSection({ agents, loading }) {

  const [selectedCategory, setSelectedCategory] = useState('all');

  const categories = [

    { value: 'all', label: 'All Categories' },

    { value: 'google_workspace', label: 'Google Workspace' },

    { value: 'internal', label: 'Internal' },

    { value: 'utility', label: 'Utility' },

  ];

  const getCategoryColor = (c) => ({ google_workspace: 'primary', internal: 'secondary', utility: 'info' }[c] || 'default');

  const filtered = selectedCategory === 'all' ? agents : agents.filter(a => a.category === selectedCategory);



  if (loading) return <Paper sx={{ p: 3, textAlign: 'center' }}><CircularProgress /><Typography sx={{ mt: 2 }} color="text.secondary">Loading system agents...</Typography></Paper>;



  return (

    <Paper sx={{ p: 2 }}>

      <Typography variant="h6" mb={1}>System Agents</Typography>

      <Typography variant="body2" color="text.secondary" mb={2}>

        Built-in agents that power Atlas. They run automatically and cannot be modified.

      </Typography>

      <FormControl fullWidth sx={{ mb: 2 }}>

        <InputLabel>Filter by Category</InputLabel>

        <Select value={selectedCategory} onChange={(e) => setSelectedCategory(e.target.value)} label="Filter by Category">

          {categories.map(cat => <MenuItem key={cat.value} value={cat.value}>{cat.label}</MenuItem>)}

        </Select>

      </FormControl>

      <Grid container spacing={2}>

        {filtered.map(agent => (

          <Grid item xs={12} md={6} key={agent.id}>

            <Card variant="outlined">

              <CardContent>

                <Box display="flex" justifyContent="space-between" alignItems="start" mb={1}>

                  <Box>

                    <Typography variant="subtitle1" fontWeight={600}>{agent.name}</Typography>

                    <Chip label={agent.category.replace('_', ' ')} size="small" color={getCategoryColor(agent.category)} sx={{ mt: 0.5, textTransform: 'capitalize' }} />

                  </Box>

                  <Chip label={`${agent.tool_count} tools`} size="small" variant="outlined" />

                </Box>

                <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>{agent.description}</Typography>

                {agent.capabilities?.length > 0 && (

                  <Box mt={1.5}>

                    <Typography variant="caption" color="text.secondary" display="block" mb={0.5}>Capabilities:</Typography>

                    <Box display="flex" flexWrap="wrap" gap={0.5}>

                      {agent.capabilities.slice(0, 5).map(cap => (

                        <Chip key={cap} label={cap.replace(/_/g, ' ')} size="small" variant="outlined" sx={{ textTransform: 'capitalize' }} />

                      ))}

                      {agent.capabilities.length > 5 && <Chip label={`+${agent.capabilities.length - 5} more`} size="small" variant="outlined" />}

                    </Box>

                  </Box>

                )}

              </CardContent>

            </Card>

          </Grid>

        ))}

      </Grid>

      {filtered.length === 0 && <Alert severity="info" sx={{ mt: 2 }}>No agents in this category.</Alert>}

    </Paper>

  );

}





// ── Organization Creation Wizard (AI streaming, 4 steps) ─────────────



const WIZARD_STEPS = ['Describe', 'AI Generates', 'Review & Edit', 'Validate', 'Confirm'];



function OrgWizardDialog({ open, onClose, onComplete }) {

  const [step, setStep] = useState(0);

  const [submitting, setSubmitting] = useState(false);

  const [submitError, setSubmitError] = useState(null);

  const [catalog, setCatalog] = useState({ skills: [], tools: [] });



  // Step 0 form

  const [form, setForm] = useState({ name: '', goal: '', description: '', budget_cap_usd: '' });



  // Step 1 streaming state

  const [streaming, setStreaming] = useState(false);

  const [streamError, setStreamError] = useState(null);

  const [streamDone, setStreamDone] = useState(false);

  const [streamedAgents, setStreamedAgents] = useState([]);

  const [streamedTasks, setStreamedTasks] = useState([]);

  const [fullPlan, setFullPlan] = useState(null);



  // Step 2 editable state (copied from streamed)

  const [editAgents, setEditAgents] = useState([]);

  const [editTasks, setEditTasks] = useState([]);



  // Step 3 plan validation (pre-submit dry run)

  const [planValidation, setPlanValidation] = useState(null);

  const [validating, setValidating] = useState(false);

  // Step 4 cohesion result (post-create)

  const [cohesion, setCohesion] = useState(null);



  useEffect(() => {

    if (open && catalog.skills.length === 0) {

      axios.get(`${API}/orgs/catalog`)

        .then(r => setCatalog({ skills: r.data.skills || [], tools: r.data.tools || [] }))

        .catch(() => {});

    }

  }, [open]);



  const resetWizard = () => {

    setStep(0);

    setForm({ name: '', goal: '', description: '', budget_cap_usd: '' });

    setStreaming(false);

    setStreamError(null);

    setStreamDone(false);

    setStreamedAgents([]);

    setStreamedTasks([]);

    setFullPlan(null);

    setEditAgents([]);

    setEditTasks([]);

    setCohesion(null);

    setPlanValidation(null);

    setRepairLog([]);

    setRepairPhase('idle');

    setLanes(Object.fromEntries(['instructions', 'skills', 'tools', 'schedule'].map(l => [l, { status: 'pending', items: [], total: 0 }])));

    setSubmitError(null);

  };



  const handleClose = () => { resetWizard(); onClose(); };



  // ── Step 1: start SSE stream ──────────────────────────────────────

  const startStream = () => {

    setStreaming(true);

    setStreamError(null);

    setStreamDone(false);

    setStreamedAgents([]);

    setStreamedTasks([]);

    setFullPlan(null);



    const params = new URLSearchParams({ goal: form.goal || form.name });

    if (form.description) params.append('description', form.description);

    if (form.name) params.append('org_name', form.name);

    const es = new EventSource(`${API}/orgs/plan-stream?${params}`);



    // Use a plain mutable ref so closures always read the live value,

    // avoiding the stale-closure bug where streamDone is always false inside es.onerror.

    const doneRef = { current: false };

    const errorRef = { current: false };



    es.onmessage = (e) => {

      try {

        const obj = JSON.parse(e.data);

        if (obj.type === 'agent') setStreamedAgents(prev => [...prev, obj]);

        else if (obj.type === 'task') setStreamedTasks(prev => [...prev, obj]);

      } catch (_) {}

    };



    es.addEventListener('done', (e) => {

      doneRef.current = true;

      try {

        const plan = JSON.parse(e.data);

        setFullPlan(plan);

        setEditAgents((plan.agents || []).map((a, i) => ({ ...a, _key: i })));

        setEditTasks((plan.tasks || []).map((t, i) => ({ ...t, _key: i })));

      } catch (_) {}

      setStreamDone(true);

      setStreaming(false);

      es.close();

    });



    es.addEventListener('error', (e) => {

      errorRef.current = true;

      let msg = 'Generation failed';

      try {

        const d = JSON.parse(e.data);

        msg = d.detail || d.message || 'Stream error';

      } catch (_) {}

      setStreamError(msg);

      setStreaming(false);

      es.close();

    });



    // onerror fires on ANY network-level issue AND on normal server close in some browsers.

    // Only show "Connection lost" if we never received a clean 'done' or named 'error' event.

    es.onerror = () => {

      if (!doneRef.current && !errorRef.current) {

        setStreamError('Connection lost — check that the orchestration API is running');

        setStreaming(false);

      }

      es.close();

    };

  };



  useEffect(() => {

    if (step === 1 && !streaming && !streamDone && !streamError) startStream();

  }, [step]); // eslint-disable-line



  // ── Step 3: 4-lane deep-repair loop (SSE) ───────────────────────
  const [repairLog, setRepairLog] = useState([]);
  const [repairPhase, setRepairPhase] = useState('idle'); // idle | running | done
  // lanes: { instructions, skills, tools, schedule }
  // each lane = { status: 'pending'|'running'|'done', items: [{name,status,score,msg,...}] }
  const LANES = ['instructions', 'skills', 'tools', 'schedule'];
  const LANE_LABELS = { instructions: 'Instructions', skills: 'Skills', tools: 'Tools', schedule: 'Schedules' };
  const LANE_ICONS = { instructions: '📋', skills: '🧠', tools: '🔧', schedule: '🕐' };
  const [lanes, setLanes] = useState(() =>
    Object.fromEntries(LANES.map(l => [l, { status: 'pending', items: [], total: 0 }]))
  );

  const _buildPlanPayload = () => ({
    ...(fullPlan || {}),
    agents: editAgents,
    tasks: editTasks,
  });

  const _updateLane = (name, updater) =>
    setLanes(prev => ({ ...prev, [name]: updater(prev[name]) }));

  const handleDeepRepair = async () => {
    setRepairPhase('running');
    setRepairLog([]);
    setPlanValidation(null);
    setLanes(Object.fromEntries(LANES.map(l => [l, { status: 'pending', items: [], total: 0 }])));

    const payload = _buildPlanPayload();

    // Open SSE stream via EventSource (GET with base64 payload)
    const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(payload))));
    const url = `${API}/orgs/deep-repair-stream?plan=${encodeURIComponent(encoded)}`;

    await new Promise((resolve) => {
      const es = new EventSource(url);

      es.addEventListener('loop_start', e => {
        const d = JSON.parse(e.data);
        _updateLane(d.loop, prev => ({ ...prev, status: 'running', total: d.total || 0 }));
        setRepairLog(prev => [...prev, { type: 'info', msg: `▶ ${LANE_LABELS[d.loop] || d.loop}: starting (${d.total} item(s))` }]);
      });

      es.addEventListener('loop_item', e => {
        const d = JSON.parse(e.data);
        _updateLane(d.loop, prev => ({
          ...prev,
          items: [...prev.items.filter(x => !(x.index === d.index && x.name === d.name)), d],
        }));
        if (d.status === 'fixed') {
          const before = d.before ? ` [${[].concat(d.before).join(', ')}]` : '';
          const after = d.after ? ` → [${[].concat(d.after).join(', ')}]` : '';
          setRepairLog(prev => [...prev, {
            type: 'fix',
            msg: `${LANE_ICONS[d.loop]} ${d.name}: ${d.msg || ''}${before}${after}`,
          }]);
        }
      });

      es.addEventListener('loop_done', e => {
        const d = JSON.parse(e.data);
        _updateLane(d.loop, prev => ({ ...prev, status: 'done' }));
      });

      es.addEventListener('done', e => {
        const d = JSON.parse(e.data);
        es.close();
        // Apply repaired plan back into editor
        if (d.plan?.agents) setEditAgents(d.plan.agents.map((a, i) => ({ ...a, _key: a._key ?? i })));
        if (d.plan?.tasks)  setEditTasks(d.plan.tasks.map((t, i) => ({ ...t, _key: t._key ?? i })));
        setPlanValidation(d.validation);
        const fixes = d.fixes_made || 0;
        setRepairLog(prev => [...prev, {
          type: d.validation?.valid ? 'success' : 'warn',
          msg: d.validation?.valid
            ? `✓ All loops complete — ${fixes} fix(es) applied. Plan is valid!`
            : `Loops complete — ${fixes} fix(es) applied. ${d.validation?.warnings?.length || 0} warning(s) remain.`,
        }]);
        setRepairPhase('done');
        resolve();
      });

      es.addEventListener('error', e => {
        es.close();
        const msg = e.data ? JSON.parse(e.data).msg : 'SSE connection error';
        setPlanValidation({ valid: false, warnings: [], errors: [msg], checks: [], score: 0, scheduled_tasks: 0, job_fn_ok: false });
        setRepairLog(prev => [...prev, { type: 'error', msg: `Deep repair failed: ${msg}` }]);
        setRepairPhase('done');
        resolve();
      });

      // Timeout safety valve — 3 min max
      setTimeout(() => { es.close(); resolve(); }, 180000);
    });
  };

  // Auto-trigger when entering step 3
  useEffect(() => {
    if (step === 3 && repairPhase === 'idle') {
      handleDeepRepair();
    }
  }, [step]); // eslint-disable-line



  // ── Step 4: create org ────────────────────────────────────────────

  const handleSubmit = async () => {

    setSubmitting(true);

    setSubmitError(null);

    setCohesion(null);

    try {

      const plan = {

        ...(fullPlan || {}),

        org_name: form.name || fullPlan?.org_name,

        org_goal: form.goal || fullPlan?.org_goal,

        budget_cap_usd: form.budget_cap_usd ? parseFloat(form.budget_cap_usd) : (fullPlan?.budget_cap_usd || 0),

        agents: editAgents,

        tasks: editTasks,

      };

      const resp = await axios.post(`${API}/orgs/setup`, {

        goal: form.goal || form.name,

        org_name: form.name,

        plan,

      });

      const orgId = resp.data.org_id;

      // Run cohesion validation

      try {

        const val = await axios.post(`${API}/orgs/${orgId}/validate`);

        setCohesion(val.data);

      } catch (_) {}

      setStep(5); // success step

      onComplete();

    } catch (e) {

      setSubmitError(e.response?.data?.detail || 'Failed to create organization');

    } finally {

      setSubmitting(false);

    }

  };



  const step0Valid = form.name.trim().length > 0 && form.goal.trim().length > 0;

  const skillIds = catalog.skills.map(s => s.id);

  const toolNames = catalog.tools.map(t => t.name);



  const updateEditAgent = (idx, field, val) => {

    setEditAgents(prev => prev.map((a, i) => i === idx ? { ...a, [field]: val } : a));

  };

  const removeEditAgent = (idx) => setEditAgents(prev => prev.filter((_, i) => i !== idx));

  const addEditAgent = () => setEditAgents(prev => [...prev, { _key: Date.now(), name: '', role: 'specialist', description: '', instructions: '', model_tier: 'general', skills: [], allowed_tools: [] }]);



  const updateEditTask = (idx, field, val) => {

    setEditTasks(prev => prev.map((t, i) => i === idx ? { ...t, [field]: val } : t));

  };

  const removeEditTask = (idx) => setEditTasks(prev => prev.filter((_, i) => i !== idx));

  const addEditTask = () => setEditTasks(prev => [...prev, { _key: Date.now(), title: '', description: '', priority: 'medium', agent_name: editAgents[0]?.name || '', goal_ancestry: [] }]);



  const tierColor = { fast: 'success', general: 'primary', capable: 'warning' };



  return (

    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth

      PaperProps={{ sx: { minHeight: 580 } }}

    >

      <DialogTitle sx={{ pb: 0 }}>

        <Box display="flex" alignItems="center" gap={1}>

          <AutoAwesome color="primary" />

          <Typography variant="h6" fontWeight={700}>Smart Organization Wizard</Typography>

        </Box>

        <Typography variant="caption" color="text.secondary">

          Describe your goal — Atlas AI designs the team, tools, and tasks for you.

        </Typography>

      </DialogTitle>



      <DialogContent sx={{ pt: 1 }}>

        {step < 5 && (

          <Stepper activeStep={step} sx={{ mb: 3 }}>

            {WIZARD_STEPS.map(label => (

              <Step key={label}><StepLabel>{label}</StepLabel></Step>

            ))}

          </Stepper>

        )}



        {/* ── Step 0: Describe ── */}

        {step === 0 && (

          <Box>

            <TextField

              fullWidth label="Organization Name *" value={form.name} required

              onChange={e => setForm({ ...form, name: e.target.value })}

              margin="normal"

              helperText="e.g., Job Hunt, Client Acquisition, Personal Research"

              autoFocus

            />

            <TextField

              fullWidth label="Goal *" value={form.goal} required

              onChange={e => setForm({ ...form, goal: e.target.value })}

              margin="normal"

              multiline rows={2}

              placeholder="What do you want to achieve? Be as specific as possible."

              helperText="AI uses this to design your agent team and task list"

            />

            <TextField

              fullWidth label="Description (optional)" value={form.description}

              onChange={e => setForm({ ...form, description: e.target.value })}

              margin="normal"

              multiline rows={2}

              placeholder="Any additional context, constraints, timeline, or requirements"

            />

            <TextField

              fullWidth label="Monthly Budget Cap (USD, 0 = unlimited)" value={form.budget_cap_usd}

              onChange={e => setForm({ ...form, budget_cap_usd: e.target.value })}

              margin="normal"

              type="number"

              inputProps={{ min: 0, step: 1 }}

              helperText="AI will also suggest a budget — you can override it"

            />

          </Box>

        )}



        {/* ── Step 1: AI Generates ── */}

        {step === 1 && (

          <Box>

            <Box display="flex" alignItems="center" gap={1} mb={1}>

              <AutoAwesome color="primary" fontSize="small" />

              <Typography variant="subtitle2" fontWeight={700}>

                {streamDone ? 'Plan ready ✓' : streaming ? 'Atlas is designing your team…' : streamError ? 'Generation failed' : 'Starting…'}

              </Typography>

              {streamDone && <Chip label={`${streamedAgents.length} agents · ${streamedTasks.length} tasks`} size="small" color="success" />}

            </Box>

            {streaming && <LinearProgress sx={{ mb: 2 }} />}

            {streamError && (

              <Alert severity="error" sx={{ mb: 2 }} action={

                <Button size="small" onClick={() => { setStreamError(null); setStreamDone(false); startStream(); }}>Retry</Button>

              }>{streamError}</Alert>

            )}



            {streamedAgents.length > 0 && (

              <Box mb={2}>

                <Typography variant="overline" color="text.secondary">Agents</Typography>

                <Box display="flex" flexDirection="column" gap={1} mt={0.5}>

                  {streamedAgents.map((a, i) => (

                    <Paper key={i} variant="outlined" sx={{ p: 1.5, display: 'flex', alignItems: 'flex-start', gap: 1 }}>

                      <Group fontSize="small" color="primary" sx={{ mt: 0.25 }} />

                      <Box flex={1}>

                        <Typography variant="body2" fontWeight={600}>{a.name}

                          <Chip label={a.model_tier || 'general'} size="small" color={tierColor[a.model_tier] || 'default'} sx={{ ml: 1, fontSize: 10 }} />

                        </Typography>

                        <Typography variant="caption" color="text.secondary">{a.description}</Typography>

                        <Box display="flex" gap={0.5} mt={0.5} flexWrap="wrap">

                          {(a.skills || []).map(s => <Chip key={s} label={s} size="small" variant="outlined" sx={{ fontSize: 10 }} />)}

                          {(a.allowed_tools || []).map(t => <Chip key={t} label={t} size="small" color="secondary" variant="outlined" sx={{ fontSize: 10 }} />)}

                        </Box>

                      </Box>

                    </Paper>

                  ))}

                </Box>

              </Box>

            )}



            {streamedTasks.length > 0 && (

              <Box>

                <Typography variant="overline" color="text.secondary">Tasks</Typography>

                <Box display="flex" flexDirection="column" gap={0.5} mt={0.5}>

                  {streamedTasks.map((t, i) => (

                    <Box key={i} display="flex" alignItems="center" gap={1} px={1} py={0.5} sx={{ borderRadius: 1, bgcolor: 'action.hover' }}>

                      <Chip label={t.priority || 'medium'} size="small"

                        color={t.priority === 'high' ? 'error' : t.priority === 'low' ? 'default' : 'warning'}

                        sx={{ fontSize: 10, minWidth: 48 }} />

                      <Typography variant="body2" flex={1}>{t.title}</Typography>

                      {t.schedule?.trigger && t.schedule.trigger !== 'none' && (
                        <Chip icon={<Schedule fontSize="small" />} size="small" color="info" variant="outlined"
                          label={t.schedule.trigger === 'cron' ? `${t.schedule.hour ?? 8}:${String(t.schedule.minute ?? 0).padStart(2,'0')} daily` : t.schedule.trigger}
                          sx={{ fontSize: 10 }} />
                      )}

                      <Typography variant="caption" color="text.secondary">{t.agent_name}</Typography>

                    </Box>

                  ))}

                </Box>

              </Box>

            )}



            {!streaming && !streamDone && !streamError && (

              <Box display="flex" justifyContent="center" py={4}><CircularProgress /></Box>

            )}

          </Box>

        )}



        {/* ── Step 2: Review & Edit ── */}

        {step === 2 && (

          <Box>

            <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>

              <Typography variant="subtitle2" fontWeight={700}>Agents ({editAgents.length})</Typography>

              <Button size="small" startIcon={<AddCircleOutline />} onClick={addEditAgent}>Add Agent</Button>

            </Box>

            <Box display="flex" flexDirection="column" gap={1.5} mb={2}>

              {editAgents.map((a, idx) => (

                <Paper key={a._key} variant="outlined" sx={{ p: 1.5 }}>

                  <Box display="flex" gap={1} flexWrap="wrap" alignItems="flex-start">

                    <TextField size="small" label="Name" value={a.name}

                      onChange={e => updateEditAgent(idx, 'name', e.target.value)} sx={{ flex: '1 1 160px' }} />

                    <TextField size="small" label="Role" value={a.role}

                      onChange={e => updateEditAgent(idx, 'role', e.target.value)} sx={{ flex: '1 1 120px' }} />

                    <TextField size="small" select label="Tier" value={a.model_tier || 'general'}

                      onChange={e => updateEditAgent(idx, 'model_tier', e.target.value)} sx={{ flex: '0 0 100px' }}>

                      {['fast', 'general', 'capable'].map(t => <MenuItem key={t} value={t}>{t}</MenuItem>)}

                    </TextField>

                    <IconButton size="small" color="error" onClick={() => removeEditAgent(idx)} sx={{ mt: 0.5 }}>

                      <Delete fontSize="small" />

                    </IconButton>

                  </Box>

                  <Box mt={1}>

                    <Typography variant="caption" color="text.secondary" display="block" mb={0.5}>Skills</Typography>

                    <Box display="flex" gap={0.5} flexWrap="wrap">

                      {(a.skills || []).map(s => (

                        <Chip key={s} label={s} size="small" onDelete={() => updateEditAgent(idx, 'skills', a.skills.filter(x => x !== s))}

                          color={skillIds.includes(s) ? 'primary' : 'default'} variant="outlined" sx={{ fontSize: 10 }} />

                      ))}

                      <TextField size="small" placeholder="+ skill" variant="standard"

                        sx={{ width: 80, fontSize: 11 }}

                        onKeyDown={e => { if (e.key === 'Enter' && e.target.value) { updateEditAgent(idx, 'skills', [...(a.skills||[]), e.target.value]); e.target.value = ''; }}} />

                    </Box>

                  </Box>

                  <Box mt={0.5}>

                    <Typography variant="caption" color="text.secondary" display="block" mb={0.5}>Tools</Typography>

                    <Box display="flex" gap={0.5} flexWrap="wrap">

                      {(a.allowed_tools || []).map(t => (

                        <Chip key={t} label={t} size="small" color="secondary" onDelete={() => updateEditAgent(idx, 'allowed_tools', a.allowed_tools.filter(x => x !== t))}

                          variant="outlined" sx={{ fontSize: 10 }}

                          icon={toolNames.includes(t) ? undefined : <Warning fontSize="small" color="warning" />} />

                      ))}

                      <TextField size="small" placeholder="+ tool" variant="standard"

                        sx={{ width: 80, fontSize: 11 }}

                        onKeyDown={e => { if (e.key === 'Enter' && e.target.value) { updateEditAgent(idx, 'allowed_tools', [...(a.allowed_tools||[]), e.target.value]); e.target.value = ''; }}} />

                    </Box>

                  </Box>

                </Paper>

              ))}

            </Box>



            <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>

              <Typography variant="subtitle2" fontWeight={700}>Tasks ({editTasks.length})</Typography>

              <Button size="small" startIcon={<AddCircleOutline />} onClick={addEditTask}>Add Task</Button>

            </Box>

            <Box display="flex" flexDirection="column" gap={1}>

              {editTasks.map((t, idx) => (

                <Paper key={t._key} variant="outlined" sx={{ p: 1.5 }}>

                  <Box display="flex" gap={1} flexWrap="wrap" alignItems="center">

                    <TextField size="small" label="Title" value={t.title}

                      onChange={e => updateEditTask(idx, 'title', e.target.value)} sx={{ flex: '1 1 200px' }} />

                    <TextField size="small" select label="Priority" value={t.priority || 'medium'}

                      onChange={e => updateEditTask(idx, 'priority', e.target.value)} sx={{ flex: '0 0 90px' }}>

                      {['high', 'medium', 'low'].map(p => <MenuItem key={p} value={p}>{p}</MenuItem>)}

                    </TextField>

                    <TextField size="small" select label="Agent" value={t.agent_name || ''}

                      onChange={e => updateEditTask(idx, 'agent_name', e.target.value)} sx={{ flex: '1 1 140px' }}>

                      {editAgents.map(a => <MenuItem key={a.name} value={a.name}>{a.name || '(unnamed)'}</MenuItem>)}

                    </TextField>

                    <IconButton size="small" color="error" onClick={() => removeEditTask(idx)}>

                      <Delete fontSize="small" />

                    </IconButton>

                  </Box>

                  {/* Schedule row */}
                  <Box display="flex" gap={1} alignItems="center" mt={1} flexWrap="wrap">
                    <TextField size="small" select label="Schedule" value={t.schedule?.trigger || 'none'}
                      onChange={e => {
                        const v = e.target.value;
                        if (v === 'none') updateEditTask(idx, 'schedule', null);
                        else updateEditTask(idx, 'schedule', { ...(t.schedule || {}), trigger: v });
                      }} sx={{ flex: '0 0 110px' }}>
                      {['none', 'cron', 'interval', 'once'].map(v => <MenuItem key={v} value={v}>{v}</MenuItem>)}
                    </TextField>
                    {t.schedule?.trigger === 'cron' && (<>
                      <TextField size="small" label="Hour (0-23)" type="number" value={t.schedule?.hour ?? 8}
                        onChange={e => updateEditTask(idx, 'schedule', { ...t.schedule, hour: parseInt(e.target.value) || 0 })}
                        inputProps={{ min: 0, max: 23 }} sx={{ flex: '0 0 100px' }} />
                      <TextField size="small" label="Minute (0-59)" type="number" value={t.schedule?.minute ?? 0}
                        onChange={e => updateEditTask(idx, 'schedule', { ...t.schedule, minute: parseInt(e.target.value) || 0 })}
                        inputProps={{ min: 0, max: 59 }} sx={{ flex: '0 0 110px' }} />
                      <TextField size="small" label="Days" value={t.schedule?.day_of_week ?? '*'}
                        onChange={e => updateEditTask(idx, 'schedule', { ...t.schedule, day_of_week: e.target.value })}
                        placeholder="* or mon,tue…" sx={{ flex: '0 0 120px' }} />
                    </>)}
                    {t.schedule?.trigger === 'interval' && (
                      <TextField size="small" label="Every N seconds" type="number" value={t.schedule?.seconds ?? 3600}
                        onChange={e => updateEditTask(idx, 'schedule', { ...t.schedule, seconds: parseInt(e.target.value) || 3600 })}
                        inputProps={{ min: 60 }} sx={{ flex: '0 0 150px' }} />
                    )}
                    {t.schedule?.trigger && t.schedule.trigger !== 'none' && (
                      <Chip size="small" icon={<Schedule fontSize="small" />} color="info" variant="outlined"
                        label={t.schedule.trigger === 'cron' ? `daily ${t.schedule.hour ?? 8}:${String(t.schedule.minute ?? 0).padStart(2,'0')}` : t.schedule.trigger} />
                    )}
                  </Box>

                </Paper>

              ))}

            </Box>

          </Box>

        )}



        {/* ── Step 3: 4-Lane Deep Repair ── */}

        {step === 3 && (

          <Box>

            {/* Header */}

            <Box display="flex" alignItems="center" gap={1} mb={1.5}>

              <VerifiedUser color={repairPhase === 'done' && planValidation?.valid ? 'success' : repairPhase === 'done' ? 'warning' : 'primary'} />

              <Box flex={1}>

                <Typography variant="subtitle2" fontWeight={700}>

                  {repairPhase === 'idle' && 'Preparing deep validation…'}

                  {repairPhase === 'running' && 'Running 4-loop agentic repair…'}

                  {repairPhase === 'done' && planValidation?.valid && '✓ All loops passed — plan is optimized!'}

                  {repairPhase === 'done' && !planValidation?.valid && 'Loops complete — review warnings below'}

                </Typography>

                <Typography variant="caption" color="text.secondary">

                  Each loop independently validates and self-repairs: Instructions → Skills → Tools → Schedules

                </Typography>

              </Box>

              {repairPhase === 'running' && <CircularProgress size={18} />}

            </Box>

            {repairPhase === 'running' && <LinearProgress variant="indeterminate" sx={{ mb: 2 }} />}



            {/* 4-Lane Cards */}

            <Box display="grid" gridTemplateColumns="1fr 1fr" gap={1.5} mb={2}>

              {LANES.map(lane => {

                const l = lanes[lane];

                const laneColor = l.status === 'done'

                  ? (l.items.some(x => x.status === 'fixed') ? 'warning.light' : 'success.light')

                  : l.status === 'running' ? 'primary.light' : 'grey.100';

                const fixCount = l.items.filter(x => x.status === 'fixed').length;

                const okCount  = l.items.filter(x => x.status === 'ok').length;

                return (

                  <Paper key={lane} variant="outlined" sx={{ p: 1.5, borderColor: laneColor, borderWidth: 2 }}>

                    <Box display="flex" alignItems="center" gap={0.5} mb={0.75}>

                      <Typography variant="body2" fontWeight={700} sx={{ fontSize: 13 }}>

                        {LANE_ICONS[lane]} {LANE_LABELS[lane]}

                      </Typography>

                      <Box flex={1} />

                      {l.status === 'pending' && <Chip label="pending" size="small" sx={{ fontSize: 10, height: 18 }} />}

                      {l.status === 'running' && <CircularProgress size={12} />}

                      {l.status === 'done' && fixCount === 0 && <Chip label="✓ OK" size="small" color="success" sx={{ fontSize: 10, height: 18 }} />}

                      {l.status === 'done' && fixCount > 0 && <Chip label={`${fixCount} fixed`} size="small" color="warning" sx={{ fontSize: 10, height: 18 }} />}

                    </Box>

                    {l.items.length === 0 && l.status !== 'done' && (

                      <Typography variant="caption" color="text.disabled">Waiting…</Typography>

                    )}

                    {l.items.map((item, idx) => (

                      <Box key={idx} display="flex" alignItems="center" gap={0.5} mb={0.25}>

                        <Typography variant="caption" sx={{ fontSize: 10,

                          color: item.status === 'fixed' ? 'warning.dark' : item.status === 'ok' ? 'success.dark' : item.status === 'error' ? 'error.main' : 'text.secondary'

                        }}>

                          {item.status === 'fixed' ? '⚒' : item.status === 'ok' ? '✓' : item.status === 'error' ? '✗' : '…'}

                        </Typography>

                        <Typography variant="caption" flex={1} noWrap sx={{ fontSize: 10 }}>{item.name}</Typography>

                        {item.score != null && (

                          <Chip label={`${item.score}`} size="small"

                            color={item.score >= 80 ? 'success' : item.score >= 60 ? 'warning' : 'error'}

                            sx={{ fontSize: 9, height: 16, '& .MuiChip-label': { px: 0.5 } }} />

                        )}

                      </Box>

                    ))}

                    {l.status === 'done' && (

                      <Typography variant="caption" color="text.secondary" sx={{ fontSize: 10 }}>

                        {okCount} OK · {fixCount} fixed

                      </Typography>

                    )}

                  </Paper>

                );

              })}

            </Box>



            {/* Repair activity log */}

            {repairLog.length > 0 && (

              <Paper variant="outlined" sx={{ p: 1.5, mb: 1.5, maxHeight: 150, overflowY: 'auto', bgcolor: 'grey.50' }}>

                <Typography variant="caption" color="text.secondary" display="block" mb={0.25} fontWeight={600}>

                  Repair Log

                </Typography>

                {repairLog.map((entry, i) => (

                  <Box key={i} mb={0.1}>

                    <Typography variant="caption" sx={{

                      color: entry.type === 'fix' ? 'warning.dark' : entry.type === 'success' ? 'success.main'

                        : entry.type === 'error' ? 'error.main' : entry.type === 'warn' ? 'warning.main' : 'info.main',

                      fontFamily: 'monospace', fontSize: 11, lineHeight: 1.4, display: 'block'

                    }}>

                      {entry.msg}

                    </Typography>

                  </Box>

                ))}

              </Paper>

            )}



            {/* Final validation summary — shown after all loops done */}

            {planValidation && repairPhase === 'done' && (

              <Box>

                <Box display="flex" alignItems="center" gap={1} mb={1} flexWrap="wrap">

                  <Chip label={`Score: ${planValidation.score}/100`}

                    color={planValidation.score >= 80 ? 'success' : planValidation.score >= 60 ? 'warning' : 'error'} />

                  <Chip label={`${planValidation.scheduled_tasks} scheduled job(s)`}

                    icon={<Schedule fontSize="small" />}

                    color={planValidation.scheduled_tasks > 0 ? 'info' : 'default'} variant="outlined" />

                  <Chip label={planValidation.job_fn_ok ? 'Scheduler ✓' : 'Scheduler ✗'}

                    color={planValidation.job_fn_ok ? 'success' : 'error'} variant="outlined" />

                </Box>

                {planValidation.errors?.map((e, i) => <Alert key={i} severity="error" sx={{ mb: 0.5 }}>{e}</Alert>)}

                {planValidation.warnings?.map((w, i) => <Alert key={i} severity="warning" sx={{ mb: 0.5 }}>{w}</Alert>)}

                {planValidation.valid && !planValidation.warnings?.length && (

                  <Alert severity="success" icon={<CheckCircle />}>

                    All 4 loops passed — agents, skills, tools, and schedules are fully optimized. Ready to create!

                  </Alert>

                )}

                <Box mt={1} display="flex" gap={1}>

                  <Button size="small" startIcon={<Refresh />}

                    onClick={() => { setRepairLog([]); setRepairPhase('idle'); setLanes(Object.fromEntries(LANES.map(l => [l, { status: 'pending', items: [], total: 0 }]))); handleDeepRepair(); }}

                    disabled={repairPhase !== 'done'}>

                    Re-run All Loops

                  </Button>

                </Box>

              </Box>

            )}

          </Box>

        )}



        {/* ── Step 4: Confirm ── */}

        {step === 4 && (

          <Box>

            <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>

              <Typography variant="overline" color="text.secondary" display="block">Organization</Typography>

              <Typography variant="h6">{form.name}</Typography>

              <Typography variant="body2" color="text.secondary">{form.goal}</Typography>

              {form.budget_cap_usd && <Chip label={`Budget: $${form.budget_cap_usd}/mo`} size="small" icon={<AttachMoney />} sx={{ mt: 1 }} />}

            </Paper>

            <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>

              <Typography variant="overline" color="text.secondary" display="block" mb={1}>

                {editAgents.length} Agent{editAgents.length !== 1 ? 's' : ''}

              </Typography>

              {editAgents.map((a, i) => (

                <Box key={i} display="flex" alignItems="center" gap={1} mb={0.5}>

                  <Group fontSize="small" color="primary" />

                  <Typography variant="body2" fontWeight={600}>{a.name}</Typography>

                  <Chip label={a.model_tier || 'general'} size="small" color={tierColor[a.model_tier] || 'default'} sx={{ fontSize: 10 }} />

                  <Typography variant="caption" color="text.secondary">{(a.skills||[]).join(', ')}</Typography>

                </Box>

              ))}

            </Paper>

            <Paper variant="outlined" sx={{ p: 2 }}>

              <Typography variant="overline" color="text.secondary" display="block" mb={1}>

                {editTasks.length} Task{editTasks.length !== 1 ? 's' : ''}

              </Typography>

              {editTasks.map((t, i) => (

                <Box key={i} display="flex" alignItems="center" gap={1} mb={0.5}>

                  <Chip label={t.priority} size="small" color={t.priority === 'high' ? 'error' : t.priority === 'low' ? 'default' : 'warning'} sx={{ fontSize: 10, minWidth: 48 }} />

                  <Typography variant="body2" flex={1}>{t.title}</Typography>

                  <Typography variant="caption" color="text.secondary">{t.agent_name}</Typography>

                </Box>

              ))}

            </Paper>

            {submitError && <Alert severity="error" sx={{ mt: 2 }}>{submitError}</Alert>}

          </Box>

        )}



        {/* ── Step 5: Success + Cohesion Report ── */}

        {step === 5 && (

          <Box>

            <Alert severity="success" icon={<CheckCircle />} sx={{ mb: 2 }}>

              Organization <strong>{form.name}</strong> created successfully!

            </Alert>

            {cohesion && (

              <Paper variant="outlined" sx={{ p: 2 }}>

                <Box display="flex" alignItems="center" gap={1} mb={1}>

                  <VerifiedUser color={cohesion.valid ? 'success' : 'warning'} />

                  <Typography variant="subtitle2" fontWeight={700}>Cohesion Report</Typography>

                  <Chip label={`Score: ${cohesion.score}/100`} size="small"

                    color={cohesion.score >= 80 ? 'success' : cohesion.score >= 60 ? 'warning' : 'error'} />

                </Box>

                {cohesion.errors.length > 0 && cohesion.errors.map((e, i) => (

                  <Alert key={i} severity="error" sx={{ mb: 0.5 }}>{e}</Alert>

                ))}

                {cohesion.warnings.length > 0 && cohesion.warnings.map((w, i) => (

                  <Alert key={i} severity="warning" sx={{ mb: 0.5 }}>{w}</Alert>

                ))}

                {cohesion.valid && cohesion.warnings.length === 0 && (

                  <Typography variant="body2" color="success.main">✓ All agents, skills, and tools validated.</Typography>

                )}

              </Paper>

            )}

          </Box>

        )}

      </DialogContent>



      <DialogActions sx={{ px: 3, pb: 2 }}>

        {step < 5 ? (

          <Button onClick={handleClose} disabled={submitting}>Cancel</Button>

        ) : (

          <Button onClick={handleClose}>Close</Button>

        )}

        <Box flex={1} />

        {step > 0 && step < 5 && (

          <Button onClick={() => setStep(s => s - 1)} disabled={submitting || streaming}>Back</Button>

        )}

        {step === 0 && (

          <Button variant="contained" onClick={() => setStep(1)} disabled={!step0Valid} startIcon={<AutoAwesome />}>

            Generate Plan

          </Button>

        )}

        {step === 1 && (

          <Button variant="contained" onClick={() => setStep(2)} disabled={!streamDone || !!streamError}>

            Review & Edit

          </Button>

        )}

        {step === 2 && (

          <Button variant="contained" onClick={() => { setPlanValidation(null); setRepairLog([]); setRepairPhase('idle'); setLanes(Object.fromEntries(['instructions','skills','tools','schedule'].map(l => [l,{status:'pending',items:[],total:0}]))); setStep(3); }} disabled={editAgents.length === 0}>

            Validate Plan

          </Button>

        )}

        {step === 3 && (

          <Button variant="contained" onClick={() => setStep(4)} disabled={!planValidation}

            color={planValidation?.valid ? 'success' : 'warning'}>

            {planValidation?.valid ? 'Confirm & Create' : 'Create Anyway'}

          </Button>

        )}

        {step === 4 && (

          <Button variant="contained" color="success" onClick={handleSubmit} disabled={submitting}

            startIcon={submitting ? <CircularProgress size={16} /> : <CheckCircle />}>

            {submitting ? 'Creating…' : 'Create Organization'}

          </Button>

        )}

        {step === 5 && (

          <Button variant="outlined" onClick={() => { resetWizard(); onComplete(); }}>

            View Organizations

          </Button>

        )}

      </DialogActions>

    </Dialog>

  );

}





export default Dashboard;

