import React from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Button,
  IconButton,
  LinearProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Select,
  MenuItem
} from '@mui/material';
import {
  Assignment,
  PlayArrow,
  CheckCircle,
  Error,
  Schedule,
  MoreVert
} from '@mui/icons-material';

const TaskQueue = ({ tasks, agents }) => {
  const [selectedTask, setSelectedTask] = React.useState(null);
  const [detailsDialogOpen, setDetailsDialogOpen] = React.useState(false);

  const getStatusChipColor = (status) => {
    const map = {
      pending: 'default',
      assigned: 'info',
      in_progress: 'warning',
      completed: 'success',
      failed: 'error',
      cancelled: 'default',
    };
    return map[status] || 'default';
  };

  const getPriorityChipColor = (priority) => {
    const map = {
      low: 'default',
      medium: 'info',
      high: 'warning',
      critical: 'error',
    };
    return map[priority] || 'default';
  };

  const getPriorityBorderColor = (priority) => {
    const map = {
      low: '#9e9e9e',
      medium: '#2196f3',
      high: '#ff9800',
      critical: '#f44336',
    };
    return map[priority] || '#9e9e9e';
  };

  const getAgentName = (agentId) => {
    const agent = agents.find(a => a.id === agentId);
    return agent ? agent.name : 'Unassigned';
  };

  const handleAssignTask = async (taskId, agentId) => {
    try {
      const response = await fetch(`/api/tasks/${taskId}/assign`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ agent_id: agentId }),
      });
      
      if (response.ok) {
        window.location.reload();
      }
    } catch (error) {
      console.error('Failed to assign task:', error);
    }
  };

  const handleCompleteTask = async (taskId, result) => {
    try {
      const response = await fetch(`/api/tasks/${taskId}/complete`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
          result: result,
          actual_cost: 10.0
        }),
      });
      
      if (response.ok) {
        window.location.reload();
      }
    } catch (error) {
      console.error('Failed to complete task:', error);
    }
  };

  const getProgress = (task) => {
    if (task.status === 'completed') return 100;
    if (task.status === 'failed' || task.status === 'cancelled') return 0;
    if (task.status === 'in_progress') return 50;
    if (task.status === 'assigned') return 25;
    return 0;
  };

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          <Assignment sx={{ mr: 1 }} />
          Task Queue
        </Typography>
        
        <List>
          {tasks.map((task) => (
            <ListItem
              key={task.id}
              divider
              sx={{
                borderLeft: `4px solid ${getPriorityBorderColor(task.priority)}`,
                pl: 2
              }}
            >
              <ListItemIcon>
                {task.status === 'completed' && <CheckCircle color="success" />}
                {task.status === 'failed' && <Error color="error" />}
                {task.status === 'in_progress' && <PlayArrow color="warning" />}
                {task.status === 'assigned' && <Schedule color="info" />}
                {task.status === 'pending' && <Assignment color="disabled" />}
              </ListItemIcon>
              <ListItemText
                primary={
                  <Box display="flex" alignItems="center" justifyContent="space-between">
                    <Typography variant="subtitle1">
                      {task.title}
                    </Typography>
                    <Box>
                      <Chip
                        label={task.status}
                        size="small"
                        color={getStatusChipColor(task.status)}
                        sx={{ mr: 1 }}
                      />
                      <Chip
                        label={task.priority}
                        size="small"
                        color={getPriorityChipColor(task.priority)}
                        sx={{ mr: 1 }}
                      />
                      {task.assigned_agent_id && (
                        <Chip
                          label={getAgentName(task.assigned_agent_id)}
                          size="small"
                          variant="outlined"
                        />
                      )}
                    </Box>
                  </Box>
                }
                secondary={
                  <Box>
                    <Typography variant="body2" color="textSecondary" sx={{ mb: 1 }}>
                      {task.description}
                    </Typography>
                    
                    {task.goal_ancestry && task.goal_ancestry.length > 0 && (
                      <Box sx={{ mb: 1 }}>
                        <Typography variant="caption" color="textSecondary">
                          Goal Path:
                        </Typography>
                        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                          {task.goal_ancestry.map((goal, index) => (
                            <Chip
                              key={index}
                              label={goal}
                              size="small"
                              variant="outlined"
                              sx={{ fontSize: '0.7rem' }}
                            />
                          ))}
                        </Box>
                      </Box>
                    )}
                    
                    <Box display="flex" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
                      <Typography variant="caption" color="textSecondary">
                        Created: {new Date(task.created_at).toLocaleString()}
                      </Typography>
                      {task.due_at && (
                        <Typography variant="caption" color="textSecondary">
                          Due: {new Date(task.due_at).toLocaleString()}
                        </Typography>
                      )}
                    </Box>
                    
                    <Box display="flex" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
                      <Typography variant="caption" color="textSecondary">
                        Budget: ${task.budget_allocated}
                      </Typography>
                      <Typography variant="caption" color="textSecondary">
                        Cost: ${task.actual_cost}
                      </Typography>
                    </Box>
                    
                    <Box sx={{ mb: 1 }}>
                      <LinearProgress
                        variant="determinate"
                        value={getProgress(task)}
                        color={getProgress(task) === 100 ? 'success' : getProgress(task) === 0 ? 'error' : 'primary'}
                        sx={{ height: 6, borderRadius: 3 }}
                      />
                    </Box>
                    
                    <Box display="flex" gap={1}>
                      {task.status === 'pending' && (
                        <Button
                          size="small"
                          variant="contained"
                          onClick={() => {
                            setSelectedTask(task);
                          }}
                        >
                          Assign
                        </Button>
                      )}
                      
                      {task.status === 'in_progress' && (
                        <Button
                          size="small"
                          variant="outlined"
                          color="warning"
                          onClick={() => {
                            handleCompleteTask(task.id, 'Task completed successfully');
                          }}
                        >
                          Complete
                        </Button>
                      )}
                      
                      <IconButton
                        size="small"
                        onClick={() => {
                          setSelectedTask(task);
                          setDetailsDialogOpen(true);
                        }}
                      >
                        <MoreVert />
                      </IconButton>
                    </Box>
                  </Box>
                }
              />
            </ListItem>
          ))}
        </List>
        
        {/* Task Details Dialog */}
        <Dialog
          open={detailsDialogOpen}
          onClose={() => {
            setDetailsDialogOpen(false);
            setSelectedTask(null);
          }}
          maxWidth="md"
          fullWidth
        >
          <DialogTitle>Task Details</DialogTitle>
          <DialogContent>
            {selectedTask && (
              <Box>
                <Typography variant="h6" gutterBottom>
                  {selectedTask.title}
                </Typography>
                
                <Typography variant="body2" paragraph>
                  {selectedTask.description}
                </Typography>
                
                <Typography variant="subtitle1" gutterBottom>
                  Goal Ancestry
                </Typography>
                {selectedTask.goal_ancestry && selectedTask.goal_ancestry.length > 0 && (
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2 }}>
                    {selectedTask.goal_ancestry.map((goal, index) => (
                      <Chip
                        key={index}
                        label={goal}
                        size="small"
                        color="primary"
                        variant="outlined"
                      />
                    ))}
                  </Box>
                )}
                
                <Typography variant="subtitle1" gutterBottom>
                  Dependencies
                </Typography>
                {selectedTask.depends_on && selectedTask.depends_on.length > 0 ? (
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2 }}>
                    {selectedTask.depends_on.map((depId, index) => (
                      <Chip
                        key={index}
                        label={`Depends on: ${depId}`}
                        size="small"
                        color="warning"
                        variant="outlined"
                      />
                    ))}
                  </Box>
                ) : (
                  <Typography variant="body2" color="textSecondary">
                    No dependencies
                  </Typography>
                )}
                
                <Typography variant="subtitle1" gutterBottom>
                  Progress
                </Typography>
                <LinearProgress
                  variant="determinate"
                  value={getProgress(selectedTask)}
                  color={getProgress(selectedTask) === 100 ? 'success' : getProgress(selectedTask) === 0 ? 'error' : 'primary'}
                  sx={{ height: 8, borderRadius: 4 }}
                />
              </Box>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setDetailsDialogOpen(false)}>
              Close
            </Button>
          </DialogActions>
        </Dialog>
        
        {/* Agent Assignment Dialog */}
        <Dialog
          open={!!selectedTask && !detailsDialogOpen}
          onClose={() => setSelectedTask(null)}
          maxWidth="sm"
          fullWidth
        >
          <DialogTitle>Assign Task</DialogTitle>
          <DialogContent>
            {selectedTask && (
              <Box>
                <Typography variant="h6" gutterBottom>
                  {selectedTask.title}
                </Typography>
                <Typography variant="body2" paragraph>
                  Select an agent to assign this task:
                </Typography>
                
                <Select
                  fullWidth
                  label="Agent"
                  value=""
                  onChange={(e) => {
                    if (e.target.value) {
                      handleAssignTask(selectedTask.id, e.target.value);
                    }
                  }}
                  sx={{ mt: 2 }}
                >
                  <MenuItem value="">
                    <em>Unassigned</em>
                  </MenuItem>
                  {agents
                    .filter(agent => agent.is_available)
                    .map(agent => (
                      <MenuItem key={agent.id} value={agent.id}>
                        {agent.name} ({agent.role})
                      </MenuItem>
                    ))}
                </Select>
              </Box>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setSelectedTask(null)}>
              Cancel
            </Button>
          </DialogActions>
        </Dialog>
      </CardContent>
    </Card>
  );
};

export default TaskQueue;
