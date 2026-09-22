// Grammar and validation belong to the official SDK. This bridge does no I/O
// other than reading one JSON request and writing its analysis.
import {
  ScopesSet, isStaticScopeValue, RepoPermission, RpcPermission, BlobPermission,
  AccountPermission, IdentityPermission, IncludeScope,
} from '@atproto/oauth-scopes';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const { scope } = JSON.parse(input);
const permissions = [];
const unrecognized = [];
const parsers = [
  ['repo', RepoPermission], ['rpc', RpcPermission], ['blob', BlobPermission],
  ['account', AccountPermission], ['identity', IdentityPermission], ['include', IncludeScope],
];
for (const token of ScopesSet.fromString(scope)) {
  if (!token) continue;
  if (isStaticScopeValue(token)) {
    permissions.push({ kind: token === 'atproto' ? 'atproto' : 'transition', raw: token });
    continue;
  }
  let recognized = false;
  for (const [kind, parser] of parsers) {
    const value = parser.fromString(token);
    if (!value) continue;
    permissions.push({ kind, raw: token, ...value,
      ...(kind === 'include' ? { resolution: 'unresolved' } : {}) });
    recognized = true;
    break;
  }
  if (!recognized) unrecognized.push(token);
}
process.stdout.write(JSON.stringify({
  status: unrecognized.length ? 'partial' : (permissions.length ? 'parsed' : 'empty'),
  permissions, unrecognized,
  atproto_only: permissions.length === 1 && permissions[0].kind === 'atproto' && !unrecognized.length,
}));
