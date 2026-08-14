/**
 * Shared auth/profile types — mirror backend CamelBaseModel payloads.
 *
 * Backend (FastAPI + pydantic CamelBaseModel) returns camelCase JSON;
 * these interfaces match the wire format exactly. Do NOT add snake_case
 * aliases here — keep one source of truth.
 */
export type AuthProvider = 'local' | 'azure_ad';

export interface User {
  id: string;
  email: string;
  fullName: string;
  authProvider: AuthProvider;
  isActive: boolean;
  emailVerified: boolean;
  roles: Role[];
}

export interface Role {
  id: string;
  name: string;          // e.g. "internal:super_admin"
  displayName: string;   // e.g. "Super Admin"
  providerScope: 'internal' | 'external' | 'any';
  isSystem: boolean;
}

export interface MenuItem {
  id: string;
  key: string;
  labelTh: string;
  labelEn: string;
  icon: string | null;       // lucide icon name (e.g. "Users")
  path: string | null;       // leaf route; null for parent
  parentId: string | null;
  order: number;
  permissionKey: string | null;
  children?: MenuItem[];
}

export interface LoginResponse {
  accessToken: string;
  tokenType: string;     // "bearer"
  expiresIn: number;     // seconds
}

export interface PublicAuthSettings {
  /** App-settings subset that is publicly readable (no token required). */
  authLocalEnabled: boolean;
  authSsoEnabled: boolean;
}

// ---------------------------------------------------------------------------
// Phase C — admin page types. Backend payload mirrors below.
// ---------------------------------------------------------------------------

export interface Permission {
  id: string;
  key: string;          // e.g. "users.read"
  displayName: string;
  category: string;     // grouping label, e.g. "menu" / "users" / "roles"
  isMenu: boolean;      // surfaced in the sidebar permission picker
}

export interface RoleSummary {
  id: string;
  name: string;            // "internal:super_admin"
  displayName: string;
  providerScope: 'internal' | 'external' | 'any';
  isSystem: boolean;
  // Optional — only included on the list endpoint for the admin Roles page.
  // Backend may or may not populate; treat as 0 when missing.
  usersCount?: number;
}

export interface RoleDetail extends RoleSummary {
  description: string | null;
  permissions: Permission[];
}

export interface UserSummary {
  id: string;
  email: string;
  fullName: string;
  authProvider: AuthProvider;
  isActive: boolean;
  isApproved: boolean;
  lastLoginAt: string | null;
  roles: RoleSummary[];
  supplierId: string | null;
  isSupplierAdmin: boolean;
}

export interface UserDetail extends UserSummary {
  emailVerified: boolean;
  businessUnitIds: string[];
  createdAt: string;
  updatedAt: string;
}

export interface UserOverride {
  permissionKey: string;
  granted: boolean;
  reason: string | null;
}

export interface MenuItemTree {
  id: string;
  key: string;
  labelTh: string;
  labelEn: string;
  icon: string | null;
  path: string;
  parentId: string | null;
  orderIndex: number;
  requiredPermissionKey: string;
  isSystem: boolean;
  children: MenuItemTree[];
}

export interface AppSettingValue {
  id: string;
  key: string;
  value: unknown;
  valueType: string;
  category: string;
  description: string | null;
  requiresRole: string;
  updatedAt: string;
}

export interface AuthSettingsState {
  /** Effective values keyed by app-setting key. */
  authLocalEnabled: boolean;
  authSsoEnabled: boolean;
  authLocalSignupEnabled: boolean;
  authSignupDefaultRole: string;
}

export interface SystemLog {
  id: string;
  createdAt: string;
  category: string;
  event: string;
  status: string;          // "started" | "success" | "failure" | "warning" | "info"
  durationMs: number | null;
  errorType: string | null;
  errorMessage: string | null;
  correlationId: string | null;
  extraMetadata: Record<string, unknown>;
}

export interface ActivityLog {
  id: string;
  createdAt: string;
  userId: string | null;
  userEmailMasked: string | null;
  action: string;            // dotted event name, e.g. "auth.login_failed"
  actionType: string;        // "login" | "login_failed" | "logout" | "create" | ...
  resourceType: string | null;
  resourceId: string | null;
  riskLevel: string;         // "low" | "medium" | "high"
  isSecurityEvent: boolean;
  ipAddress: string | null;
  httpStatus: number | null;
  extraMetadata: Record<string, unknown>;
}
