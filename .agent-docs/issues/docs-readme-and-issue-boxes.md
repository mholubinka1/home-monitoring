# Issues: docs-readme-and-issue-boxes

> Work complete — PR ready to merge.

## docs: README still says database must be octopus; tick two stale issue-file boxes — [#582](https://github.com/mholubinka1/home-monitoring/issues/582)

**Blocked by**: None

### What to build

Correct the two README statements that still name the pre-rename `octopus`
database (the `database` setting and the first-run description) to
`home_monitoring`, which `deployments/mariadb/docker-compose.yml` and
`data/mariadb/init.sql` create. Check both app config templates for the same
claim. Tick the two stale boxes in
`.agent-docs/issues/feature-hive-ntfy-reauth-notifications.md` once their
checks are verified.

### Acceptance criteria

- [x] Given the README, no statement names `octopus` as the database the apps
      connect to or MariaDB creates.
- [x] Both app config templates were checked: `database:` is blank in each, so
      nothing is stale there.
- [x] The two `docker compose config` boxes in the reauth-notifications issues
      file are ticked, after the combined compose file's `config` exited 0 and
      the hive-app compose log mount was confirmed.
