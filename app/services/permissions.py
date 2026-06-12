"""PermissionService — RBAC granular por entidad + columna.

Consulta las tablas rol, usuario_rol, rol_permiso_entidad y
rol_permiso_columna para determinar qué puede hacer un usuario.
"""
from __future__ import annotations

from sqlalchemy import text


class PermissionService:
    def __init__(self, db, user):
        self.db = db
        self.user = user
        self._roles: list[dict] = []
        self._permisos: dict[str, dict[str, bool]] = {}
        self._cols_ocultas: dict[str, set[str]] = {}
        self._cols_editables: dict[str, set[str]] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Carga de datos
    # ------------------------------------------------------------------
    def _load(self):
        if self._loaded:
            return
        self._load_roles()
        self._load_permisos_entidad()
        self._load_permisos_columna()
        self._loaded = True

    def _load_roles(self):
        """Carga todos los roles del usuario: base + personalizados."""
        rows = self.db.execute(
            text("""
                SELECT DISTINCT r.id, r.nombre, r.es_base, r.base_rol, r.descripcion
                FROM emergencias.rol r
                LEFT JOIN emergencias.usuario_rol ur ON ur.rol_id = r.id
                WHERE ur.usuario_id = :uid
                  AND r.activo = TRUE
            """),
            {
                "uid": self.user.id,
            },
        ).mappings().all()
        # Si es base role (ej: CONDUCTOR) y no aparece en usuario_rol,
        # buscar por tenant + base_rol
        if not rows and self.user.rol in (
            "ADMIN_PLATAFORMA", "ADMIN_TENANT", "CONDUCTOR", "TALLER", "TECNICO"
        ):
            rows = self.db.execute(
                text("""
                    SELECT r.id, r.nombre, r.es_base, r.base_rol, r.descripcion
                    FROM emergencias.rol r
                    WHERE r.tenant_id IS NOT DISTINCT FROM :tid
                      AND r.es_base = TRUE
                      AND r.base_rol = :base_rol
                      AND r.activo = TRUE
                """),
                {
                    "tid": self.user.tenant,
                    "base_rol": self.user.rol,
                },
            ).mappings().all()
        self._roles = [dict(r) for r in rows]

    @property
    def role_ids(self) -> list[str]:
        return [r["id"] for r in self._roles]

    def _load_permisos_entidad(self):
        """Carga permisos CRUD por entidad (OR entre roles)."""
        if not self.role_ids:
            return
        rows = self.db.execute(
            text("""
                SELECT entidad,
                       bool_or(puede_crear)    AS c,
                       bool_or(puede_leer)     AS r,
                       bool_or(puede_actualizar) AS u,
                       bool_or(puede_eliminar) AS d
                FROM emergencias.rol_permiso_entidad
                WHERE rol_id = ANY(:rids)
                GROUP BY entidad
            """),
            {"rids": self.role_ids},
        ).mappings().all()
        for row in rows:
            self._permisos[row["entidad"]] = {
                "crear": row["c"],
                "leer": row["r"],
                "actualizar": row["u"],
                "eliminar": row["d"],
            }

    def _load_permisos_columna(self):
        """Carga restricciones de columna (AND restrictivo: si algún
        rol oculta, la columna se oculta)."""
        if not self.role_ids:
            return
        rows = self.db.execute(
            text("""
                SELECT entidad, columna,
                       bool_and(puede_ver)   AS v,
                       bool_or(puede_editar) AS e
                FROM emergencias.rol_permiso_columna
                WHERE rol_id = ANY(:rids)
                GROUP BY entidad, columna
            """),
            {"rids": self.role_ids},
        ).mappings().all()
        for row in rows:
            entidad = row["entidad"]
            if entidad not in self._cols_ocultas:
                self._cols_ocultas[entidad] = set()
                self._cols_editables[entidad] = set()
            if not row["v"]:
                self._cols_ocultas[entidad].add(row["columna"])
            if row["e"]:
                self._cols_editables[entidad].add(row["columna"])

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def can(self, entidad: str, accion: str) -> bool:
        """Verifica si el usuario tiene permiso en la entidad+acción.

        accion debe ser: 'crear', 'leer', 'actualizar' o 'eliminar'.
        Si no hay permisos definidos para esa entidad, permite todo
        (para backward compat con endpoints que aún usan require_roles).
        """
        self._load()
        perm = self._permisos.get(entidad)
        if perm is None:
            return True
        return perm.get(accion, False)

    def visible_columns(self, entidad: str) -> set[str] | None:
        """Devuelve el set de columnas visibles, o None si todas son visibles."""
        self._load()
        ocultas = self._cols_ocultas.get(entidad)
        if not ocultas:
            return None
        return ocultas

    def editable_columns(self, entidad: str) -> set[str] | None:
        """Columnas que el usuario puede modificar. None = todas editables."""
        self._load()
        editables = self._cols_editables.get(entidad)
        if not editables:
            return None
        return editables

    def filter_dict(self, entidad: str, data: dict) -> dict:
        """Elimina columnas no visibles del diccionario."""
        self._load()
        ocultas = self._cols_ocultas.get(entidad)
        if not ocultas:
            return data
        return {k: v for k, v in data.items() if k not in ocultas}

    def filter_list(self, entidad: str, items: list[dict]) -> list[dict]:
        """Aplica filter_dict a cada elemento de la lista."""
        return [self.filter_dict(entidad, item) for item in items]

    def get_permission_matrix(self) -> dict:
        """Devuelve la matriz completa de permisos: entidad → {c,r,u,d}."""
        self._load()
        default = {"crear": False, "leer": False, "actualizar": False, "eliminar": False}
        matrix = {}
        for entidad, perm in self._permisos.items():
            matrix[entidad] = perm.copy()
        return matrix

    def get_hidden_columns(self) -> dict[str, list[str]]:
        """Devuelve columnas ocultas por entidad (para el frontend)."""
        self._load()
        return {entidad: sorted(cols) for entidad, cols in self._cols_ocultas.items()}

    def get_full_permissions(self) -> dict:
        """Respuesta completa para GET /me/permisos."""
        self._load()
        return {
            "roles": [
                {"id": r["id"], "nombre": r["nombre"], "es_base": r["es_base"]}
                for r in self._roles
            ],
            "permisos": self.get_permission_matrix(),
            "columnas_ocultas": self.get_hidden_columns(),
        }
