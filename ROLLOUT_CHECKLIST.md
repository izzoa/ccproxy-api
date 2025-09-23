# Codex Parity Improvements - Rollout Checklist

This checklist outlines the steps for safely deploying the Codex parity improvements to production.

## Pre-Deployment Verification

### Code Review
- [ ] All changes reviewed by at least one other developer
- [ ] No blocking comments or concerns remain unresolved
- [ ] Code follows project style guidelines and conventions

### Testing
- [ ] All unit tests passing (`pytest tests/unit/`)
- [ ] Integration tests passing (`pytest tests/integration/`)
- [ ] Manual testing completed for:
  - [ ] Basic Codex responses endpoint
  - [ ] Chat completions endpoint with tool calling
  - [ ] Session management (auto and persistent)
  - [ ] Instruction injection modes (override, append, disabled)
  - [ ] Parameter propagation (temperature, top_p, etc.)
  - [ ] Streaming responses
  - [ ] Error handling and fallbacks

### Documentation
- [ ] README.md updated with new features
- [ ] CHANGELOG.md entry completed
- [ ] API documentation updated (if applicable)
- [ ] Configuration options documented

## Deployment Steps

### 1. Environment Preparation
- [ ] Backup current configuration
- [ ] Review environment variables for new settings:
  - `CODEX__SYSTEM_PROMPT_INJECTION_MODE`
  - `CODEX__ENABLE_DYNAMIC_MODEL_INFO`
  - `CODEX__MAX_OUTPUT_TOKENS_FALLBACK`
  - `CODEX__PROPAGATE_UNSUPPORTED_PARAMS`
- [ ] Verify CLI overrides where needed using `ccproxy codex set`
- [ ] Ensure monitoring/alerting is configured for new metrics

### 2. Gradual Rollout

#### Stage 1: Internal Testing (Day 1-2)
- [ ] Deploy to staging environment
- [ ] Run smoke tests:
  ```bash
  # Test basic connectivity
  ccproxy codex info

  # Verify cache management
  ccproxy codex cache

  # Confirm configuration overrides
  ccproxy codex set --enable-dynamic-model-info --max-output-tokens-fallback 8192
  ```
- [ ] Monitor metrics for anomalies
- [ ] Test with internal tools/applications

#### Stage 2: Limited Production (Day 3-5)
- [ ] Deploy to 10% of production traffic
- [ ] Monitor:
  - [ ] Request success rates
  - [ ] Response times
  - [ ] Token usage and costs
  - [ ] Error rates
- [ ] Collect user feedback
- [ ] Address any critical issues

#### Stage 3: Full Production (Day 6+)
- [ ] Deploy to remaining production traffic
- [ ] Continue monitoring for 48 hours
- [ ] Document any issues and resolutions

### 3. Feature Flags (Optional)

Consider using feature flags for gradual enablement:

```bash
# Start with conservative defaults
CODEX__SYSTEM_PROMPT_INJECTION_MODE=disabled
CODEX__ENABLE_DYNAMIC_MODEL_INFO=false

# Gradually enable features
CODEX__SYSTEM_PROMPT_INJECTION_MODE=append
CODEX__ENABLE_DYNAMIC_MODEL_INFO=true
```

## Rollback Plan

### Quick Rollback
If critical issues are discovered:

1. **Immediate Actions:**
   - [ ] Revert to previous version
   - [ ] Clear Codex detection cache: `ccproxy codex cache --clear`
   - [ ] Restore previous configuration

2. **Communication:**
   - [ ] Notify affected users
   - [ ] Document issue in incident report
   - [ ] Create fix plan

### Partial Rollback
For non-critical issues:

1. **Disable Specific Features:**
   ```bash
   # Disable new features while keeping improvements
   CODEX__SYSTEM_PROMPT_INJECTION_MODE=disabled
   CODEX__ENABLE_DYNAMIC_MODEL_INFO=false
   ```

2. **Monitor & Fix:**
   - [ ] Continue monitoring existing features
   - [ ] Deploy fixes incrementally
   - [ ] Re-enable features after verification

## Post-Deployment

### Monitoring Checklist (First 48 Hours)

- [ ] **Metrics to Watch:**
  - Codex request volume
  - Response times (P50, P95, P99)
  - Error rates by endpoint
  - Token consumption patterns
  - Cost tracking

- [ ] **Prometheus Queries:**
  ```promql
  # Codex request rate
  rate(request_count{service_type="codex"}[5m])
  
  # Error rate
  rate(request_count{service_type="codex",status_code=~"5.."}[5m])
  
  # Response time
  histogram_quantile(0.95, rate(request_duration_seconds_bucket{service_type="codex"}[5m]))
  ```

- [ ] **Log Analysis:**
  - Check for new error patterns
  - Verify instruction injection is working
  - Monitor tool call success rates

### Success Criteria

The rollout is considered successful when:

- [ ] No increase in error rates > 1%
- [ ] Response times remain within SLA
- [ ] No critical bugs reported
- [ ] User feedback is positive or neutral
- [ ] All monitoring dashboards are green

### Follow-up Actions

- [ ] Schedule retrospective meeting
- [ ] Document lessons learned
- [ ] Update runbooks with new procedures
- [ ] Plan next iteration of improvements

## Contacts

### Escalation Path
1. On-call engineer
2. Codex parity implementation team
3. Platform team lead
4. Engineering manager

### Key Stakeholders
- Product Owner: [Name]
- Technical Lead: [Name]
- QA Lead: [Name]
- DevOps Lead: [Name]

## Appendix: Quick Commands

```bash
# Check Codex status
ccproxy codex test

# View current configuration
ccproxy config show | grep -i codex

# Monitor logs
tail -f /var/log/ccproxy/access.log | grep codex

# Clear cache if needed
ccproxy codex cache --clear

# Emergency disable
export CODEX__ENABLED=false
systemctl restart ccproxy
```

## Sign-offs

- [ ] Development Team
- [ ] QA Team
- [ ] DevOps Team
- [ ] Product Owner
- [ ] Engineering Manager

---

**Document Version:** 1.0
**Last Updated:** 2024-01-23
**Next Review:** Post-deployment retrospective
