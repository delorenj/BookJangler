# ProgressTracker.md Maintenance Standards

## 1. Structure and Hierarchy

### 1.1 Phase Level

- Maximum of 3-4 major phases per project
- Phases represent significant milestones or logical groupings
- Each phase must be independently deployable or testable
- Phase naming must reflect concrete outcomes (e.g., "MVP Development" not "Initial Phase")

### 1.2 Feature Level

- Maximum of 3-5 major features per phase
- Features must represent complete, testable functionality
- Feature names should map directly to user-facing capabilities
- Features should take 1-2 weeks to implement

### 1.3 Task Level

- Maximum of 4-6 tasks per feature
- Tasks must be completable within 1-2 days
- No further subdivision below task level
- Tasks must be directly actionable

## 2. Content Guidelines

### 2.1 Status Tracking

- Use consistent status indicators:
  - Not Started
  - In Progress
  - Blocked (with blocker identified)
  - Completed
- Include percentage complete at feature level only
- Task level uses binary completion status [x] or [ ]

### 2.2 Dependencies

- List blockers only when they prevent work from starting
- Reference dependent tasks by feature name, not task name
- Remove blockers as soon as they're resolved

### 2.3 Updates Section

- Keep only the last 5-7 significant updates
- Include date and concrete achievement
- Remove minor or redundant updates

## 3. Maintenance Rules

### 3.1 Task Management

- No task creation without removing or completing existing tasks
- When adding a new task, evaluate if an existing task can be merged or removed
- Maximum total tasks visible at any time: 20-25

### 3.2 Progress Updates

- Update percentages only at feature level
- Use 25% increments for progress tracking
- Update completion status first, then evaluate need for new tasks

### 3.3 Document Evolution

- Archive completed phases instead of deleting
- Review and clean up document weekly
- Maintain a single source of truth

## 4. Anti-Patterns to Avoid

### 4.1 Task Proliferation

- No creating sub-sub-tasks
- No splitting tasks unless blocked
- No duplicate tracking of related work

### 4.2 Scope Management

- No adding new phases without stakeholder approval
- No creating tasks outside current phase scope
- No tracking minor bug fixes as separate tasks

### 4.3 Status Bloat

- No tracking individual code commits
- No logging daily progress updates
- No tracking reviewer comments

## 5. Implementation Example

```markdown
## Phase 1: MVP Development

### 1. User Authentication

- [x] Implement OAuth flow
- [ ] Create user profile system
- [ ] Add session management
- [ ] Implement role-based access

Status: In Progress
Complete: 25%
Notes: OAuth implementation done, blocked by user DB setup

### 2. Core Feature X

[Tasks...]
```

## 6. Document Maintenance Checklist

Weekly Review:

- [ ] Remove completed tasks older than 2 weeks
- [ ] Verify all blockers are still relevant
- [ ] Update phase percentages
- [ ] Archive completed phases
- [ ] Remove redundant updates
