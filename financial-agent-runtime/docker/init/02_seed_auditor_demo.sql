INSERT INTO tenants (tenant_id, display_name, status)
VALUES ('tenant-b', 'Tenant B', 'active')
ON CONFLICT DO NOTHING;

INSERT INTO organizations (organization_id, tenant_id, name, status)
VALUES ('org-b', 'tenant-b', 'Org B', 'active')
ON CONFLICT DO NOTHING;

INSERT INTO users (user_id, display_name, email, status)
VALUES ('auditor-01', 'Auditor User', 'auditor-01@example.com', 'active')
ON CONFLICT DO NOTHING;

INSERT INTO tenant_memberships (membership_id, tenant_id, user_id, organization_id, status)
VALUES (gen_random_uuid(), 'tenant-b', 'auditor-01', 'org-b', 'active')
ON CONFLICT (tenant_id, user_id) DO UPDATE SET status = 'active', organization_id = 'org-b';

INSERT INTO user_roles (user_role_id, tenant_id, user_id, role_id, granted_by)
VALUES (gen_random_uuid(), 'tenant-b', 'auditor-01', 'auditor', 'seed')
ON CONFLICT (tenant_id, user_id, role_id) DO NOTHING;
