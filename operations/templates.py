"""Jinja2 configuration template management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, StrictUndefined

logger = logging.getLogger(__name__)


class TemplateManager:
    """Render, list, save, and retrieve Jinja2 configuration templates.

    When the manager is instantiated it creates the template directory (if
    absent) and writes a set of built-in starter templates so they are always
    available.
    """

    # ------------------------------------------------------------------
    # Built-in templates (class constants)
    # ------------------------------------------------------------------

    VLAN_TEMPLATE = """\
{# Create one or more VLANs on a switch. #}
{% for vlan in vlans %}
vlan {{ vlan.id }}
 name {{ vlan.name }}
{% if vlan.description is defined %}
 description {{ vlan.description }}
{% endif %}
{% endfor %}
"""

    INTERFACE_TEMPLATE = """\
{# Configure a layer-2 or layer-3 interface. #}
interface {{ interface_name }}
{% if description is defined and description %}
 description {{ description }}
{% endif %}
{% if ip_address is defined and ip_address %}
 ip address {{ ip_address }} {{ subnet_mask | default("255.255.255.0") }}
 no shutdown
{% endif %}
{% if switchport_mode is defined %}
 switchport mode {{ switchport_mode }}
{% if switchport_mode == "access" and access_vlan is defined %}
 switchport access vlan {{ access_vlan }}
{% elif switchport_mode == "trunk" %}
{% if trunk_allowed_vlans is defined %}
 switchport trunk allowed vlan {{ trunk_allowed_vlans }}
{% endif %}
{% if trunk_native_vlan is defined %}
 switchport trunk native vlan {{ trunk_native_vlan }}
{% endif %}
{% endif %}
{% endif %}
{% if shutdown is defined and shutdown %}
 shutdown
{% else %}
 no shutdown
{% endif %}
"""

    ACL_TEMPLATE = """\
{# Create an extended IP access-list. #}
ip access-list extended {{ acl_name }}
{% for entry in entries %}
 {{ entry.action }} {{ entry.protocol | default("ip") }}{% if entry.source is defined %} {{ entry.source }}{% else %} any{% endif %}{% if entry.destination is defined %} {{ entry.destination }}{% else %} any{% endif %}{% if entry.port is defined %} eq {{ entry.port }}{% endif %}{% if entry.log is defined and entry.log %} log{% endif %}

{% endfor %}
"""

    STATIC_ROUTE_TEMPLATE = """\
{# Add one or more static routes. #}
{% for route in routes %}
ip route {{ route.network }} {{ route.mask }} {{ route.next_hop }}{% if route.name is defined %} name {{ route.name }}{% endif %}{% if route.admin_distance is defined %} {{ route.admin_distance }}{% endif %}

{% endfor %}
"""

    _BUILTIN_TEMPLATES: dict[str, str] = {
        "vlan.j2": VLAN_TEMPLATE,
        "interface.j2": INTERFACE_TEMPLATE,
        "acl.j2": ACL_TEMPLATE,
        "static_route.j2": STATIC_ROUTE_TEMPLATE,
    }

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def __init__(self, template_dir: str = "./data/templates") -> None:
        self._template_dir = Path(template_dir)
        self._template_dir.mkdir(parents=True, exist_ok=True)
        self._seed_builtins()
        self._env = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    def _seed_builtins(self) -> None:
        """Write built-in templates to disk if they do not already exist."""
        for name, content in self._BUILTIN_TEMPLATES.items():
            path = self._template_dir / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")
                logger.info("Seeded built-in template %s.", name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(self, template_name: str, variables: dict[str, Any]) -> str:
        """Render a template file with the provided variables.

        Parameters
        ----------
        template_name:
            File name relative to the template directory (e.g. ``"vlan.j2"``).
        variables:
            A mapping of variable names to values fed into the template.

        Returns
        -------
        str
            The rendered configuration text.

        Raises
        ------
        FileNotFoundError
            If the template does not exist.
        """
        try:
            template = self._env.get_template(template_name)
        except TemplateNotFound as exc:
            raise FileNotFoundError(
                f"Template {template_name!r} not found in {self._template_dir}"
            ) from exc
        rendered = template.render(**variables)
        logger.debug(
            "Rendered template %s (%d chars).", template_name, len(rendered)
        )
        return rendered

    def list_templates(self) -> list[str]:
        """Return a sorted list of template file names in the template
        directory (including subdirectories, relative paths)."""
        templates: list[str] = []
        for path in self._template_dir.rglob("*"):
            if path.is_file() and path.suffix in (".j2", ".jinja2", ".tmpl", ".cfg"):
                templates.append(str(path.relative_to(self._template_dir)))
        return sorted(templates)

    def save_template(self, name: str, content: str) -> None:
        """Save or overwrite a template file.

        Intermediate directories are created automatically so that names like
        ``"vendor/cisco/bgp.j2"`` work as expected.
        """
        path = self._template_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        # Invalidate Jinja2 cache so the updated template is used immediately.
        self._env = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        logger.info("Saved template %s (%d bytes).", name, len(content))

    def get_template(self, name: str) -> str:
        """Return the raw content of a template file.

        Raises
        ------
        FileNotFoundError
            If the template does not exist.
        """
        path = self._template_dir / name
        if not path.is_file():
            raise FileNotFoundError(
                f"Template {name!r} not found in {self._template_dir}"
            )
        return path.read_text(encoding="utf-8")
