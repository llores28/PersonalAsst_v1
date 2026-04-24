import React from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  Avatar,
  LinearProgress,
  Tooltip
} from '@mui/material';
import {
  AccountTree,
} from '@mui/icons-material';
import { green, orange, red, blue, purple, cyan, amber, grey } from '@mui/material/colors';

const OrgChart = ({ agents }) => {
  // Build hierarchy from agents data
  const buildHierarchy = (agents) => {
    const nodeMap = new Map();
    const rootNodes = [];
    
    // Create nodes for each agent
    agents.forEach(agent => {
      nodeMap.set(agent.id, {
        id: agent.id,
        name: agent.name,
        role: agent.role,
        isAvailable: agent.is_available,
        budget: agent.monthly_budget,
        spent: agent.current_month_spend,
        currentTask: agent.current_task_id,
        children: [],
        parent: agent.parent_agent_id
      });
    });
    
    // Build tree structure
    agents.forEach(agent => {
      const node = nodeMap.get(agent.id);
      if (agent.parent_agent_id && nodeMap.has(agent.parent_agent_id)) {
        const parent = nodeMap.get(agent.parent_agent_id);
        parent.children.push(node);
      } else {
        rootNodes.push(node);
      }
    });
    
    return rootNodes;
  };

  const getRoleColor = (role) => {
    const colors = {
      ceo: blue[500],
      cto: green[500],
      developer: orange[500],
      analyst: purple[500],
      coordinator: cyan[500],
      specialist: amber[500],
      assistant: grey[500],
    };
    return colors[role] || grey[500];
  };

  const getStatusColor = (isAvailable, currentTaskId) => {
    if (!isAvailable && currentTaskId) return red[500];
    if (!isAvailable) return orange[500];
    return green[500];
  };

  const getBudgetUtilization = (budget, spent) => {
    const utilization = (spent / budget) * 100;
    return Math.min(utilization, 100);
  };

  const renderNode = (node, level = 0) => {
    const utilization = getBudgetUtilization(node.budget, node.spent);
    
    return (
      <Box key={node.id} sx={{ ml: level * 4 }}>
        <Card
          sx={{
            backgroundColor: 'background.paper',
            border: `2px solid ${getRoleColor(node.role)}`,
            borderRadius: 2,
            mb: 1,
            position: 'relative',
            overflow: 'visible'
          }}
        >
          <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
            <Box display="flex" alignItems="center" mb={2}>
              <Avatar
                sx={{
                  bgcolor: getRoleColor(node.role),
                  color: 'white',
                  mr: 2,
                  width: 40,
                  height: 40
                }}
              >
                {node.role.charAt(0).toUpperCase()}
              </Avatar>
              <Box flex={1}>
                <Typography variant="h6" component="div">
                  {node.name}
                </Typography>
                <Typography variant="body2" color="textSecondary">
                  {node.description || `${node.role} agent`}
                </Typography>
              </Box>
              <Tooltip title={`Status: ${node.isAvailable ? 'Available' : 'Busy'}`}>
                <Chip
                  size="small"
                  label={node.isAvailable ? 'Available' : 'Busy'}
                  color={node.isAvailable ? 'success' : 'warning'}
                  sx={{ ml: 1 }}
                />
              </Tooltip>
            </Box>
            
            <Box display="flex" alignItems="center" justifyContent="space-between" mb={1}>
              <Typography variant="caption" color="textSecondary">
                Budget: ${node.budget}/mo
              </Typography>
              <Typography variant="caption" color="textSecondary">
                Spent: ${node.spent.toFixed(2)}
              </Typography>
            </Box>
            
            <Box mb={1}>
              <Box display="flex" justifyContent="space-between" alignItems="center" mb={0.5}>
                <Typography variant="caption" color="textSecondary">
                  Utilization
                </Typography>
                <Typography variant="caption" color="textSecondary">
                  {utilization.toFixed(1)}%
                </Typography>
              </Box>
              <LinearProgress
                variant="determinate"
                value={utilization}
                color={utilization > 80 ? 'error' : utilization > 60 ? 'warning' : 'success'}
                sx={{ height: 8, borderRadius: 4 }}
              />
            </Box>
            
            {node.currentTaskId && (
              <Box>
                <Typography variant="caption" color="textSecondary">
                  Current Task: {node.currentTaskId.slice(0, 12)}...
                </Typography>
              </Box>
            )}
            
            {node.children.length > 0 && (
              <Box mt={2}>
                <Typography variant="subtitle2" color="textSecondary" sx={{ mb: 2 }}>
                  Direct Reports:
                </Typography>
                {node.children.map(child => renderNode(child, level + 1))}
              </Box>
            )}
          </CardContent>
        </Card>
      </Box>
    );
  };

  const hierarchy = buildHierarchy(agents);

  return (
    <Box>
      <Typography variant="h6" gutterBottom>
        <AccountTree sx={{ mr: 1, verticalAlign: 'middle' }} />
        Organization Hierarchy
      </Typography>
      {hierarchy.length === 0 ? (
        <Typography variant="body2" color="textSecondary" sx={{ textAlign: 'center', mt: 4 }}>
          No agents configured. Create your first agent to get started.
        </Typography>
      ) : (
        hierarchy.map(node => renderNode(node))
      )}
    </Box>
  );
};

export default OrgChart;
