package plattform.authz

default allow := false

# Deterministische ABAC-Autorisierung für Mandanten-Isolation, Schutzklassen und Freigabestufen (L1-L3)
allow if {
    input.user.tenant_id == input.resource.tenant_id
    input.user.clearance_level >= input.resource.schutzklasse
    valid_action
}

valid_action if {
    input.action == "skill.detect"
}

valid_action if {
    input.action == "skill.execute"
    input.resource.skill_id in input.tenant.enabled_skills
}

valid_action if {
    input.action == "workbench.approve"
    input.user.approval_role in {"L1", "L2", "L3"}
}
