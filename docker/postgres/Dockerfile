FROM postgres:17

COPY set-app-role.sh /docker-entrypoint-initdb.d/10-set-app-role.sh
COPY pg_hba.conf.template /docker-entrypoint-initdb.d/pg_hba.conf.template
COPY set-hba.sh /docker-entrypoint-initdb.d/20-set-hba.sh

# chmod happens inside the Linux build context -- not subject to macOS
# Docker Desktop bind-mount permission quirks, since these files are
# baked into the image layer rather than mounted at container runtime.
RUN chmod +x /docker-entrypoint-initdb.d/10-set-app-role.sh \
             /docker-entrypoint-initdb.d/20-set-hba.sh