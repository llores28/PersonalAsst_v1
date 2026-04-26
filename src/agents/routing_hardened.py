"""
Hardened Model Routing and Complexity Classification System

Based on research from:
- Requesty: Intelligent LLM Routing in Enterprise AI (2025)
- arXiv: A Unified Approach to Routing and Cascading for LLMs (2024)
- LangChain: Router Architecture Patterns
- Enterprise best practices for model selection and failover
"""

import re
import logging
import time
from typing import Dict, List, Tuple, Optional, Set
from enum import Enum
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger(__name__)


class TaskDomain(Enum):
    """Primary task domains for routing decisions."""
    WORKSPACE = "workspace"  # Google Workspace, Office 365, etc.
    CODE = "code"  # Programming, development
    ANALYSIS = "analysis"  # Data analysis, reports
    CREATIVE = "creative"  # Writing, content creation
    REPAIR = "repair"  # System debugging, diagnostics
    ORG_PROJECT = "org_project"  # Organization/project/team setup, media automation
    GENERAL = "general"  # Default fallback


class TaskIntent(Enum):
    """User intent categories for fine-grained routing."""
    SEARCH = "search"  # Find, list, query
    EXECUTE = "execute"  # Create, update, delete, move
    ANALYZE = "analyze"  # Summarize, compare, evaluate
    DEBUG = "debug"  # Fix, troubleshoot, repair
    CONVERSE = "converse"  # Chat, clarification
    PARALLEL = "parallel"  # Multi-domain: spawn parallel agents


@dataclass
class RoutingSignal:
    """Structured routing signal with confidence scores."""
    domain: TaskDomain
    intent: TaskIntent
    confidence: float
    complexity_score: float
    tool_requirements: Set[str]
    keywords: Set[str]


_PARALLEL_CONJUNCTIONS = (
    " and ",
    " also ",
    " as well as ",
    " plus ",
    " additionally ",
    " then ",
    " while ",
    " at the same time ",
    " simultaneously ",
)

_PARALLEL_DOMAIN_KEYWORDS: dict[str, set[str]] = {
    "gmail": {"email", "gmail", "inbox", "mail", "unread", "message"},
    "calendar": {"calendar", "schedule", "event", "meeting", "appointment", "when"},
    "drive": {"drive", "file", "folder", "document", "doc", "sheet", "slides"},
    "tasks": {"task", "todo", "to-do", "to do", "checklist"},
    "memory": {"remember", "memory", "note", "remind", "store"},
    "scheduler": {"schedule", "job", "scheduled", "recurring", "run every"},
}
_PARALLEL_CONFIDENCE_THRESHOLD = 0.70
_PARALLEL_MIN_DOMAINS = 2


def detect_parallel_domains(message: str) -> list[dict[str, str]] | None:
    """Detect whether a message requests independent tasks across multiple domains.

    Returns a list of {domain, prompt} dicts if ≥ PARALLEL_MIN_DOMAINS independent
    domains are detected AND a conjunction keyword is present — otherwise None.

    The caller should only use parallel execution when this returns a non-None value.
    """
    lowered = message.lower()

    has_conjunction = any(c in lowered for c in _PARALLEL_CONJUNCTIONS)
    if not has_conjunction:
        return None

    matched: list[str] = []
    for domain, keywords in _PARALLEL_DOMAIN_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            matched.append(domain)

    if len(matched) < _PARALLEL_MIN_DOMAINS:
        return None

    import re as _re
    parts = [p.strip() for p in _re.split(r"\band\b|\balso\b|\bplus\b", lowered, flags=_re.I) if p.strip()]
    if len(parts) < 2:
        return None

    results: list[dict[str, str]] = []
    for domain in matched[:3]:
        keywords = _PARALLEL_DOMAIN_KEYWORDS[domain]
        best_part = next((p for p in parts if any(kw in p for kw in keywords)), message)
        results.append({"domain": domain, "prompt": best_part.strip()})

    return results if len(results) >= _PARALLEL_MIN_DOMAINS else None


class HardenedClassifier:
    """
    Hardened complexity classifier with multiple detection strategies.
    
    Implements enterprise-grade routing patterns:
    1. Multi-layer classification (domain + intent)
    2. Confidence scoring
    3. Tool requirement analysis
    4. Fallback mechanisms
    5. Performance optimization
    """
    
    # Domain keyword mappings (expanded and organized)
    DOMAIN_KEYWORDS = {
        TaskDomain.WORKSPACE: {
            "contexts": {
                "drive", "gmail", "calendar", "google", "workspace", "sheets", 
                "slides", "docs", "contacts", "tasks", "email", "onedrive",
                "outlook", "office365", "sharepoint", "notion", "slack",
                "teams", "zoom", "meet", "folder", "file", "document"
            },
            "verbs": {
                "search", "list", "find", "check", "look up", "look for",
                "get", "show", "fetch", "read", "open", "browse", "scan",
                "query", "execute", "move", "organize", "rename", "share",
                "schedule", "create", "delete", "upload", "download"
            }
        },
        TaskDomain.CODE: {
            "contexts": {
                "code", "python", "javascript", "java", "sql", "api",
                "function", "class", "method", "algorithm", "script",
                "program", "development", "debug", "test", "git", "github"
            },
            "verbs": {
                "write", "code", "implement", "debug", "fix", "refactor",
                "optimize", "test", "deploy", "merge", "commit", "push"
            }
        },
        TaskDomain.ANALYSIS: {
            "contexts": {
                "data", "report", "analyze", "summary", "statistics",
                "metrics", "chart", "graph", "trend", "insight", "dashboard"
            },
            "verbs": {
                "analyze", "summarize", "compare", "evaluate", "report",
                "calculate", "measure", "track", "monitor"
            }
        },
        TaskDomain.REPAIR: {
            "contexts": {
                "error", "bug", "issue", "problem", "broken", "failed",
                "crash", "timeout", "exception", "debug", "troubleshoot"
            },
            "verbs": {
                "fix", "repair", "debug", "troubleshoot", "diagnose",
                "resolve", "recover", "restore", "reset"
            }
        },
        TaskDomain.ORG_PROJECT: {
            "contexts": {
                "organization", "org", "project", "team", "agent team",
                "project team", "media", "video", "audio", "ffmpeg",
                "imagemagick", "ffprobe", "sox", "yt-dlp", "convert",
                "clip", "slideshow", "subtitle", "caption", "overlay",
                "encode", "transcode", "codec", "bitrate", "resolution",
                "aspect ratio", "reformat", "export", "compress",
                "workflow", "pipeline", "automation", "batch",
                "cli tool", "cli", "tool creation", "create tool",
            },
            "verbs": {
                "set up", "setup", "create", "build", "establish",
                "launch", "start", "initialize", "configure", "plan",
                "generate", "produce", "make", "compose", "assemble",
                "add agent", "add task", "assign", "create org",
            }
        }
    }
    
    # Intent patterns (regex-based for precision)
    INTENT_PATTERNS = {
        TaskIntent.SEARCH: [
            r"\b(search|find|list|show|get|look)\s+(for|up|out)\b",
            r"\b(search|find|list|show|get|look)\s+\w+\s+(in|on|from)\b",
            r"\bwhat\s+(is|are|do|does)\s+\w+\b",
            r"\bhow\s+(many|much)\s+\w+\b"
        ],
        TaskIntent.EXECUTE: [
            r"\b(create|make|build|generate|write|draft)\b",
            r"\b(update|modify|edit|change|rename)\b",
            r"\b(delete|remove|archive|hide)\b",
            r"\b(move|copy|transfer|send)\b",
            r"\b(execute|run|perform|carry\s+out)\b",
            r"\b(schedule|set\s+up|arrange)\b"
        ],
        TaskIntent.ANALYZE: [
            r"\b(analyze|analysis|review|examine|inspect)\b",
            r"\b(summarize|summary|recap|outline)\b",
            r"\b(compare|contrast|versus|vs)\b",
            r"\b(evaluate|assess|rate|score)\b",
            r"\b(report|dashboard|metrics|statistics)\b"
        ],
        TaskIntent.DEBUG: [
            r"\b(debug|fix|repair|troubleshoot)\b",
            r"\b(error|issue|problem|broken|failed)\b",
            r"\b(not\s+working|won't\s+work|can't)\b",
            r"\b(why\s+is|why\s+does|what's\s+wrong)\b"
        ]
    }
    
    # Complexity indicators with weights
    COMPLEXITY_INDICATORS = {
        "high": {
            "multi_service": 3.0,  # Involves multiple services
            "cross_reference": 2.5,  # References across domains
            "deep_reasoning": 2.0,  # Requires deep analysis
            "creative_generation": 1.8,  # Creative content
            "multi_step": 1.5,  # Multiple sequential steps
        },
        "medium": {
            "single_service": 2.0,  # One service but complex
            "write_operation": 1.5,  # Create/update/delete
            "moderate_analysis": 1.3,  # Standard analysis
            "tool_heavy": 1.2,  # Requires many tools
        },
        "low": {
            "simple_read": 1.0,  # Basic lookup
            "status_check": 0.8,  # Status/health check
            "single_fact": 0.5,  # One piece of info
            "confirmation": 0.3,  # Yes/no, simple
        }
    }
    
    @classmethod
    @lru_cache(maxsize=1000)
    def classify(cls, user_message: str) -> RoutingSignal:
        """
        Classify user message with confidence scoring.
        
        Returns a RoutingSignal with:
        - Primary domain and intent
        - Confidence score (0.0-1.0)
        - Complexity score (0.0-3.0+)
        - Required tools
        - Matched keywords
        """
        if not user_message or not user_message.strip():
            return cls._create_fallback_signal()
        
        # Preprocess message
        normalized = cls._preprocess_message(user_message)
        
        # Multi-layer classification
        domain_scores = cls._classify_domain(normalized)
        intent_scores = cls._classify_intent(normalized)
        complexity_score = cls._calculate_complexity(normalized, domain_scores, intent_scores)
        tool_requirements = cls._identify_tool_requirements(normalized, domain_scores)
        
        # Select primary domain and intent
        primary_domain = max(domain_scores, key=domain_scores.get)
        primary_intent = max(intent_scores, key=intent_scores.get)
        
        # Calculate overall confidence
        confidence = cls._calculate_confidence(
            domain_scores[primary_domain],
            intent_scores[primary_intent],
            len(normalized.split())
        )
        
        # Extract matched keywords
        keywords = cls._extract_keywords(normalized, primary_domain)
        
        return RoutingSignal(
            domain=primary_domain,
            intent=primary_intent,
            confidence=confidence,
            complexity_score=complexity_score,
            tool_requirements=tool_requirements,
            keywords=keywords
        )
    
    @staticmethod
    def _preprocess_message(message: str) -> str:
        """Normalize message for classification."""
        # Remove extra whitespace, convert to lowercase
        normalized = " ".join(message.strip().lower().split())
        
        # Expand common contractions
        expansions = {
            "don't": "do not",
            "won't": "will not",
            "can't": "cannot",
            "it's": "it is",
            "that's": "that is",
            "what's": "what is",
            "let's": "let us",
        }
        
        for contraction, expansion in expansions.items():
            normalized = normalized.replace(contraction, expansion)
        
        return normalized
    
    @classmethod
    def _classify_domain(cls, normalized: str) -> Dict[TaskDomain, float]:
        """Classify task domain with fuzzy matching."""
        scores = {}
        
        for domain, config in cls.DOMAIN_KEYWORDS.items():
            score = 0.0
            
            # Context matching (higher weight)
            for context in config["contexts"]:
                if context in normalized:
                    score += 1.0
            
            # Verb matching
            for verb in config["verbs"]:
                if verb in normalized:
                    score += 0.8
            
            # Partial matching (lower weight)
            for context in config["contexts"]:
                if any(word in normalized for word in context.split('_')):
                    score += 0.3
            
            scores[domain] = score
        
        # Normalize scores
        total = sum(scores.values())
        if total > 0:
            scores = {k: v/total for k, v in scores.items()}
        else:
            scores[TaskDomain.GENERAL] = 1.0
        
        return scores
    
    @classmethod
    def _classify_intent(cls, normalized: str) -> Dict[TaskIntent, float]:
        """Classify user intent using regex patterns."""
        scores = {intent: 0.0 for intent in TaskIntent}
        
        for intent, patterns in cls.INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, normalized, re.IGNORECASE):
                    scores[intent] += 1.0
        
        # Check for conversational intent
        conversational_cues = {"hi", "hello", "thanks", "bye", "ok", "yes", "no", "sure"}
        if any(cue in normalized.split() for cue in conversational_cues):
            scores[TaskIntent.CONVERSE] += 0.5
        
        # Normalize scores
        total = sum(scores.values())
        if total > 0:
            scores = {k: v/total for k, v in scores.items()}
        else:
            scores[TaskIntent.CONVERSE] = 1.0
        
        return scores
    
    @classmethod
    def _calculate_complexity(
        cls, 
        normalized: str, 
        domain_scores: Dict[TaskDomain, float],
        intent_scores: Dict[TaskIntent, float]
    ) -> float:
        """Calculate complexity score based on multiple factors."""
        score = 0.0
        
        # Base complexity from intent.
        # NOTE: "moderate_analysis" lives in the MEDIUM tier (see
        # COMPLEXITY_INDICATORS above). Looking it up under "high" raises
        # KeyError, which silently dropped every ANALYZE-intent message into
        # the heuristic fallback and degraded production routing.
        if intent_scores.get(TaskIntent.ANALYZE, 0) > 0.5:
            score += cls.COMPLEXITY_INDICATORS["medium"]["moderate_analysis"]
        elif intent_scores.get(TaskIntent.EXECUTE, 0) > 0.5:
            score += cls.COMPLEXITY_INDICATORS["medium"]["write_operation"]
        elif intent_scores.get(TaskIntent.SEARCH, 0) > 0.5:
            score += cls.COMPLEXITY_INDICATORS["low"]["simple_read"]
        
        # Adjust for domain complexity
        if domain_scores.get(TaskDomain.WORKSPACE, 0) > 0.5:
            # Check for multi-service operations
            services = sum(1 for service in ["drive", "gmail", "calendar"] if service in normalized)
            if services > 1:
                score += cls.COMPLEXITY_INDICATORS["high"]["multi_service"]
        
        # Word count factor
        word_count = len(normalized.split())
        if word_count > 20:
            score += cls.COMPLEXITY_INDICATORS["high"]["multi_step"]
        elif word_count > 10:
            score += cls.COMPLEXITY_INDICATORS["medium"]["tool_heavy"]
        
        # Specific complexity keywords
        high_keywords = ["analyze", "compare", "summarize", "plan", "review all"]
        for keyword in high_keywords:
            if keyword in normalized:
                score += cls.COMPLEXITY_INDICATORS["high"]["deep_reasoning"]
                break
        
        return score
    
    @staticmethod
    def _identify_tool_requirements(
        normalized: str, 
        domain_scores: Dict[TaskDomain, float]
    ) -> Set[str]:
        """Identify specific tools required for the task."""
        tools = set()
        
        if domain_scores.get(TaskDomain.WORKSPACE, 0) > 0.3:
            if "drive" in normalized:
                tools.update({"drive_search", "drive_list", "drive_move"})
            if "gmail" in normalized or "email" in normalized:
                tools.update({"gmail_search", "gmail_send"})
            if "calendar" in normalized:
                tools.update({"calendar_list", "calendar_create"})
        
        return tools
    
    @staticmethod
    def _calculate_confidence(
        domain_score: float, 
        intent_score: float, 
        word_count: int
    ) -> float:
        """Calculate overall confidence in the classification."""
        # Base confidence from domain and intent alignment
        confidence = (domain_score + intent_score) / 2
        
        # Adjust for message length (very short messages are less confident)
        if word_count <= 3:
            confidence *= 0.7
        elif word_count >= 10:
            confidence *= 1.1
        
        # Cap at 1.0
        return min(confidence, 1.0)
    
    @staticmethod
    def _extract_keywords(normalized: str, domain: TaskDomain) -> Set[str]:
        """Extract keywords that contributed to the domain classification."""
        keywords = set()
        
        if domain in HardenedClassifier.DOMAIN_KEYWORDS:
            config = HardenedClassifier.DOMAIN_KEYWORDS[domain]
            
            for context in config["contexts"]:
                if context in normalized:
                    keywords.add(context)
            
            for verb in config["verbs"]:
                if verb in normalized:
                    keywords.add(verb)
        
        return keywords
    
    @staticmethod
    def _create_fallback_signal() -> RoutingSignal:
        """Create a fallback routing signal for unclear inputs."""
        return RoutingSignal(
            domain=TaskDomain.GENERAL,
            intent=TaskIntent.CONVERSE,
            confidence=0.3,
            complexity_score=0.5,
            tool_requirements=set(),
            keywords=set()
        )


class ModelRouter:
    """
    Enterprise-grade model router with failover and cascading.
    
    Implements best practices from research:
    1. Primary/secondary model pairs
    2. Confidence-based routing
    3. Automatic failover
    4. Cost optimization
    5. Performance monitoring
    """
    
    # Model configurations (can be extended)
    MODEL_CONFIGS = {
        "ultra_fast": {
            "model": "gpt-5.4-mini",
            "cost": 0.0001,
            "capability": 0.3,
            "max_tools": 10,
        },
        "fast": {
            "model": "gpt-5.4",
            "cost": 0.002,
            "capability": 0.7,
            "max_tools": 50,
        },
        "capable": {
            "model": "gpt-5.4",
            "cost": 0.002,
            "capability": 0.7,
            "max_tools": 50,
        },
        "powerful": {
            "model": "gpt-5-turbo",
            "cost": 0.01,
            "capability": 0.9,
            "max_tools": 100,
        }
    }
    
    @classmethod
    def select_model(
        cls, 
        signal: RoutingSignal,
        available_models: List[str] = None
    ) -> Tuple[str, Dict]:
        """
        Select optimal model based on routing signal.
        
        Returns tuple of (model_name, config_dict).
        """
        # Filter available models
        if available_models:
            available_configs = {
                k: v for k, v in cls.MODEL_CONFIGS.items()
                if v["model"] in available_models
            }
        else:
            available_configs = cls.MODEL_CONFIGS
        
        # Base selection on complexity and tool requirements
        if signal.complexity_score >= 2.5:
            # High complexity - use powerful model
            tier = "powerful"
        elif signal.complexity_score >= 1.5:
            # Medium complexity - use capable model
            tier = "capable"
        elif signal.complexity_score >= 0.8:
            # Low-medium complexity - use fast model
            tier = "fast"
        else:
            # Very low complexity - use ultra-fast model
            tier = "ultra_fast"
        
        # Check if tier is available
        if tier not in available_configs:
            # Fallback to next available tier
            for fallback_tier in ["fast", "capable", "powerful"]:
                if fallback_tier in available_configs:
                    tier = fallback_tier
                    break
        
        config = available_configs[tier]
        
        # Special handling for workspace operations
        if signal.domain == TaskDomain.WORKSPACE and len(signal.tool_requirements) > 5:
            # Workspace with many tools needs capable model minimum
            if config["max_tools"] < len(signal.tool_requirements):
                for upgrade_tier in ["capable", "powerful"]:
                    if upgrade_tier in available_configs:
                        config = available_configs[upgrade_tier]
                        break
        
        return config["model"], config


# Fast-path keywords: if the message contains these, force MEDIUM routing
# so the org agent and its tools are always available.
_ORG_PROJECT_FAST_PATH_VERBS = (
    "set up", "setup", "create a project", "create project",
    "set up a project", "start a project", "build a project",
    "create an organization", "create organization",
    "new project", "new org", "create a team", "create team",
    "build a team", "set up a team",
)
_ORG_PROJECT_FAST_PATH_CONTEXTS = (
    "ffmpeg", "video composer", "media automation", "video editing",
    "audio mixing", "image to video", "imagemagick", "ffprobe",
    "yt-dlp", "batch processing", "codec", "transcode",
    "from scratch", "example setup",
)


# Integration layer with existing system
def classify_message_complexity_hardenened(user_message: str):
    """
    Hardened complexity classifier compatible with existing TaskComplexity enum.
    
    This function maintains backward compatibility while providing enhanced
    classification based on the research-backed system.
    """
    from src.models.router import TaskComplexity

    lowered = " ".join(user_message.strip().lower().split())

    # Fast-path: project/org setup or media automation requests always need
    # the org agent tools — force MEDIUM so the full toolset is available.
    has_setup_verb = any(v in lowered for v in _ORG_PROJECT_FAST_PATH_VERBS)
    has_media_context = any(c in lowered for c in _ORG_PROJECT_FAST_PATH_CONTEXTS)
    if has_setup_verb or has_media_context:
        return TaskComplexity.MEDIUM

    signal = HardenedClassifier.classify(user_message)

    # ORG_PROJECT domain always needs MEDIUM (tool-heavy multi-step)
    if signal.domain == TaskDomain.ORG_PROJECT:
        return TaskComplexity.MEDIUM

    # Map complexity score to TaskComplexity enum
    if signal.complexity_score >= 2.5:
        return TaskComplexity.HIGH
    elif signal.complexity_score >= 0.8:
        return TaskComplexity.MEDIUM
    else:
        return TaskComplexity.LOW


# Monitoring and analytics
class RoutingMetrics:
    """Collect and analyze routing metrics for continuous improvement."""
    
    def __init__(self):
        self.classifications = []
        self.model_usage = {}
        self.failovers = 0
    
    def record_classification(self, signal: RoutingSignal, model: str):
        """Record a classification decision."""
        self.classifications.append({
            "timestamp": time.time(),
            "domain": signal.domain.value,
            "intent": signal.intent.value,
            "confidence": signal.confidence,
            "complexity": signal.complexity_score,
            "model": model,
            "tool_count": len(signal.tool_requirements)
        })
        
        # Track model usage
        self.model_usage[model] = self.model_usage.get(model, 0) + 1
    
    def get_success_rate(self) -> float:
        """Calculate success rate based on confidence scores."""
        if not self.classifications:
            return 0.0
        
        high_confidence = sum(1 for c in self.classifications if c["confidence"] > 0.7)
        return high_confidence / len(self.classifications)
    
    def get_average_complexity(self) -> float:
        """Get average complexity score."""
        if not self.classifications:
            return 0.0
        
        return sum(c["complexity"] for c in self.classifications) / len(self.classifications)


# Global metrics instance
routing_metrics = RoutingMetrics()
