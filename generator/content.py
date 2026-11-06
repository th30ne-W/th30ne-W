from dataclasses import dataclass


@dataclass(frozen=True)
class Project:
    slug: str
    title: str
    kind: str  # "app" | "private"
    description: str
    tags: str
    url: str


# title, kind, description and tags are copied verbatim from the site's _data/projects.yml
PROJECTS: tuple[Project, ...] = (
    Project(
        slug="dockstay",
        title="DockStay",
        kind="app",
        description=(
            "Menu-bar macOS app that keeps the Dock pinned to the display you choose and recovers "
            "automatically after sleep, display changes or disconnects."
        ),
        tags="#Swift #SwiftUI #macOS #CI",
        url="https://github.com/th30ne-W/DockStay",
    ),
    Project(
        slug="nude-notes",
        title="Nude Notes",
        kind="app",
        description=(
            "Translucent menu-bar Markdown notes for macOS: global shortcut, multi-window notes, "
            "pinning, search and auto-save to plain files."
        ),
        tags="#Tauri #Rust #React #TypeScript #macOS",
        url="https://github.com/th30ne-W/Nude-Notes",
    ),
    Project(
        slug="laya",
        title="Laya Computer Use",
        kind="private",
        description=(
            "Local macOS computer-use agent in which every action decision comes from an on-device "
            "Apple Silicon model, paired with capture tooling and an offline evaluation harness that "
            "scores decision strategies."
        ),
        tags="#Python #macOS #MLX #AI #Automation",
        url="https://williamnunes.com/projects/",
    ),
    Project(
        slug="homelab",
        title="Homelab",
        kind="private",
        description=(
            "Single-node Proxmox lab running n8n, Home Assistant and an AI agent, managed with Ansible: "
            "snapshot-guarded weekly updates, generated state snapshots, ADRs and runbooks."
        ),
        tags="#Proxmox #Ansible #n8n #HomeAssistant #Runbooks",
        url="https://williamnunes.com/projects/",
    ),
)
