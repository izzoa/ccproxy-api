# SDK Models Migration Plan: ccproxy/adapters/sdk/ → plugins/claude_sdk/

## Executive Summary
This plan details the migration of SDK models from the core adapters directory to the claude_sdk plugin, improving plugin self-containment and reducing core-plugin coupling.

## Current Architecture Analysis

### File Structure
```
ccproxy/adapters/sdk/
├── __init__.py (68 lines) - Exports all SDK models
└── models.py - SDK model definitions

plugins/claude_sdk/
├── client.py - Imports sdk models
├── converter.py - Imports sdk models  
├── streaming.py - Imports sdk models
├── stream_worker.py - Imports sdk models
├── handler.py - Imports sdk models
└── __init__.py - Re-exports SDK models from core
```

### Dependency Map
1. **Plugin Dependencies (6 files)**
   - All use: `from ccproxy.adapters.sdk import models as sdk_models`
   
2. **Core Dependencies (1 critical)**
   - `ccproxy/models/messages.py`: Imports `SDKContentBlock`
   - Creates circular dependency if moved without refactoring
   
3. **Test Dependencies**
   - `tests/unit/services/test_claude_sdk_client.py`
   - Multiple inline imports in test methods

## Impact Analysis

### Benefits
- **Plugin Autonomy**: SDK plugin becomes self-contained
- **Cleaner Architecture**: Removes SDK-specific code from core
- **Reduced Coupling**: Core no longer needs SDK knowledge
- **Better Maintainability**: Plugin owns its data models

### Challenges
- **Circular Dependency**: Core messages.py depends on SDK models
- **Import Updates**: 8+ files need import path changes
- **Backward Compatibility**: Existing code may break
- **Test Updates**: Test mocks and imports need adjustment

## Implementation Plan

### Phase 1: Dependency Analysis & Resolution (30 min)
1. **Analyze circular dependency**
   ```bash
   grep -n "SDKContentBlock" ccproxy/models/messages.py
   ```
2. **Determine resolution strategy**:
   - Option A: Create abstract base in core, SDK extends
   - Option B: Move affected message models to plugin
   - Option C: Use duck typing/protocols instead

3. **Document decision and rationale**

### Phase 2: Prepare Migration (45 min)
1. **Create feature branch**
   ```bash
   git checkout -b refactor/sdk-models-to-plugin
   ```

2. **Copy models to plugin**
   ```bash
   cp ccproxy/adapters/sdk/models.py plugins/claude_sdk/models.py
   ```

3. **Create compatibility layer**
   ```python
   # plugins/claude_sdk/sdk_models.py (temporary)
   from .models import *  # All SDK models
   ```

### Phase 3: Update Plugin Imports (45 min)
1. **Update plugin files** (6 files):
   ```python
   # Before: from ccproxy.adapters.sdk import models as sdk_models
   # After:  from . import models as sdk_models
   ```
   
2. **Files to update**:
   - `plugins/claude_sdk/client.py`
   - `plugins/claude_sdk/converter.py`
   - `plugins/claude_sdk/streaming.py`
   - `plugins/claude_sdk/stream_worker.py`
   - `plugins/claude_sdk/handler.py`
   - `plugins/claude_sdk/__init__.py`

3. **Update specific imports**:
   ```python
   # Before: from ccproxy.adapters.sdk.models import SDKMessage
   # After:  from .models import SDKMessage
   ```

### Phase 4: Resolve Core Dependency (30 min)
Based on Phase 1 analysis, implement chosen solution:

**Option A: Abstract Base**
```python
# ccproxy/models/base.py
class BaseContentBlock(Protocol):
    """Base content block interface"""
    pass

# plugins/claude_sdk/models.py  
class SDKContentBlock(BaseContentBlock):
    """SDK-specific implementation"""
    pass
```

**Option B: Move to Plugin**
```python
# Move affected models from ccproxy/models/messages.py
# to plugins/claude_sdk/models.py
```

### Phase 5: Update Tests (30 min)
1. **Update test imports**:
   ```python
   # tests/unit/services/test_claude_sdk_client.py
   from plugins.claude_sdk import models as sdk_models
   ```

2. **Update inline test imports**:
   - Search for: `from ccproxy.adapters.sdk.models import`
   - Replace with appropriate plugin imports

3. **Update test fixtures if needed**

### Phase 6: Add Deprecation Layer (15 min)
```python
# ccproxy/adapters/sdk/__init__.py
import warnings
warnings.warn(
    "Importing from ccproxy.adapters.sdk is deprecated. "
    "Use plugins.claude_sdk.models instead.",
    DeprecationWarning,
    stacklevel=2
)
# Temporary re-exports for backward compatibility
from plugins.claude_sdk.models import *
```

### Phase 7: Testing & Validation (45 min)
1. **Code quality checks**:
   ```bash
   make pre-commit
   ```

2. **Run tests**:
   ```bash
   make test
   make test-integration
   ```

3. **Manual testing**:
   - Test SDK authentication
   - Test message sending
   - Test streaming responses

### Phase 8: Cleanup (15 min)
1. **Remove old location** (after deprecation period):
   ```bash
   rm -rf ccproxy/adapters/sdk/
   ```

2. **Update documentation**:
   - Update CLAUDE.md
   - Update plugin development guide
   - Add migration notes to CHANGELOG.md

3. **Final validation**:
   ```bash
   make ci
   ```

## Rollback Plan
1. Keep feature branch separate
2. Tag current state: `git tag pre-sdk-migration`
3. If issues arise: `git checkout main`
4. Cherry-pick any unrelated fixes

## Success Criteria
- ✅ All tests pass
- ✅ No import errors
- ✅ SDK functionality unchanged
- ✅ Code coverage maintained
- ✅ Performance unchanged

## Timeline
- **Total Estimated Time**: 3-4 hours
- **Recommended Approach**: Complete in single session
- **Review Required**: Yes, before merging

## Notes
- Consider doing this after completing Phase 2 of IMPLEMENTATION_PLAN.md
- This change supports the broader plugin architecture refactoring
- May reveal other coupling issues to address

## Commands Summary
```bash
# Start
git checkout -b refactor/sdk-models-to-plugin

# After implementation
make pre-commit
make test
make test-integration

# If successful
git add -p  # Review changes carefully
git commit -m "refactor: migrate SDK models from core to plugin"
```

## Decision Log

### Phase 1 Decision: Circular Dependency Resolution
*Completed during implementation*

Date: 2025-08-19
Decision: Used lazy loading for MessageConverter and parse_formatted_sdk_content
Rationale: The circular dependency was caused by plugin's __init__.py importing components that needed core models. By using Python's __getattr__ for lazy loading, we defer imports until actually needed, breaking the circular dependency chain.

### Implementation Notes
*Completed*

- [x] Branch created (refactor/sdk-models-to-plugin)
- [x] Models copied to plugins/claude_sdk/models.py
- [x] Imports updated (6 plugin files + tests)
- [x] Tests passing (SDK client tests confirmed working)
- [x] Documentation updated (deprecation warnings added)
- [x] Circular dependency resolved via lazy loading