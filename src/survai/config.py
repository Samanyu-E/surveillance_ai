from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, field_validator, model_validator

Coordinate = Annotated[float, Field(ge=0, le=1)]
NormalizedPoint = tuple[Coordinate, Coordinate]


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counter_zone: list[NormalizedPoint] = Field(min_length=3)
    exit_line: tuple[NormalizedPoint, NormalizedPoint]
    wait_seconds: PositiveFloat = 10
    queue_count: PositiveInt = 3
    crowd_count: PositiveInt = 4
    bag_seconds: PositiveFloat = 4
    bag_radius: PositiveFloat = 0.17
    bag_motion: PositiveFloat = 0.035
    cooldown_seconds: PositiveFloat = 20
    snapshot_days: PositiveInt = 2
    image_size: int = Field(default=416, ge=32, multiple_of=32)
    weights: str = "yolov8n.pt"
    summary_model: str = "gpt-4.1-mini"

    @model_validator(mode="after")
    def check_geometry(self) -> Self:
        area = sum(
            ax * by - bx * ay
            for (ax, ay), (bx, by) in zip(
                self.counter_zone, self.counter_zone[1:] + self.counter_zone[:1]
            )
        )
        if abs(area) < 1e-8 or self.exit_line[0] == self.exit_line[1]:
            raise ValueError("Draw a counter polygon with area and two distinct exit endpoints")
        return self


def load_config(path: Path) -> Config:
    return Config.model_validate(yaml.safe_load(path.read_text()))


def save_config(path: Path, config: BaseModel) -> None:
    temporary = path.with_suffix(".yaml.tmp")
    temporary.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False))
    temporary.replace(path)


Polygon = Annotated[list[NormalizedPoint], Field(min_length=3)]


class WalkoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor: Literal["center", "bottom_center"] = "center"
    dining_zone: Polygon
    pay_zone: Polygon
    exit_band: Polygon | None = None
    exit_line: tuple[NormalizedPoint, NormalizedPoint] | None = None
    min_dwell_seconds: PositiveFloat = 120
    pay_dwell_seconds: PositiveFloat = 5
    counter_wait_seconds: PositiveFloat = 5
    lost_grace_seconds: PositiveFloat = 2
    occlusion_seconds: PositiveFloat = 5
    alert_cooldown_seconds: PositiveFloat = 20
    evidence_retention_days: PositiveInt = 2
    reassociation_distance: Annotated[float, Field(gt=0, le=1)] = 0.08

    @field_validator("dining_zone", "pay_zone", "exit_band")
    @classmethod
    def rectangular_zone(cls, points: list[NormalizedPoint] | None) -> list[NormalizedPoint] | None:
        if points is None:
            return None
        left, right = min(x for x, _ in points), max(x for x, _ in points)
        top, bottom = min(y for _, y in points), max(y for _, y in points)
        return [(left, top), (right, top), (right, bottom), (left, bottom)]

    @model_validator(mode="after")
    def check_geometry(self) -> Self:
        if (self.exit_band is None) == (self.exit_line is None):
            raise ValueError("Configure exactly one of exit_band and exit_line")
        if self.exit_line is not None and self.exit_line[0] == self.exit_line[1]:
            raise ValueError("Draw the exit line with two distinct endpoints")
        for name in ("dining_zone", "pay_zone", "exit_band"):
            polygon = getattr(self, name)
            if polygon is None:
                continue
            area = sum(
                ax * by - bx * ay
                for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1])
            )
            if abs(area) < 1e-8:
                raise ValueError(f"{name} must enclose an area")
        return self


def load_walkout_config(path: Path) -> WalkoutConfig:
    return WalkoutConfig.model_validate(yaml.safe_load(path.read_text()))


class CameraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    name: str
    source: int | str
    mirror: bool = True
    enabled: bool = True
    employee_marker: bool = False
    walkout_enabled: bool = True
    zones: dict[str, object] = Field(default_factory=dict)
    floor_points: list[NormalizedPoint] | None = Field(default=None, min_length=4, max_length=4)
    door_line: tuple[NormalizedPoint, NormalizedPoint] | None = None
    apron_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def check_door_line(self) -> Self:
        if self.door_line is not None and self.door_line[0] == self.door_line[1]:
            raise ValueError("Draw the doorway with two distinct endpoints")
        return self

    @property
    def visible_door_line(self) -> tuple[NormalizedPoint, NormalizedPoint] | None:
        if self.walkout_enabled and self.employee_marker:
            return None
        return self.door_line

    def layout(self, defaults: WalkoutConfig) -> WalkoutConfig:
        settings = defaults.model_dump()
        zones = dict(self.zones)
        for short, full in (("dining", "dining_zone"), ("pay", "pay_zone")):
            if short in zones:
                zones[full] = zones.pop(short)
        if "exit_line" in zones and "exit_band" not in zones:
            settings["exit_band"] = None
        if "exit_band" in zones and "exit_line" not in zones:
            settings["exit_line"] = None
        return WalkoutConfig.model_validate(settings | zones)


class IdentityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enroll_frames: PositiveInt = 10
    match_threshold: Annotated[float, Field(gt=0, le=1)] = 0.45
    ambiguity_margin: Annotated[float, Field(ge=0, le=1)] = 0.08
    min_box_height_px: PositiveInt = 120
    max_overlap_iou: Annotated[float, Field(ge=0, le=1)] = 0.3
    relink_seconds: PositiveFloat = 5
    descriptor_ema: Annotated[float, Field(gt=0, le=1)] = 0.1
    sample_every: PositiveInt = 2
    max_floor_distance: PositiveFloat = 0.20


class FusionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick_hz: PositiveFloat = 10
    max_skew_ms: PositiveFloat = 100
    plane: Literal["floor", "head"] = "floor"
    same_view_order: bool = False
    max_floor_dist: PositiveFloat = 0.18
    color_weight: Annotated[float, Field(ge=0, le=1)] = 0.15
    min_hits: PositiveInt = 3
    lost_grace_seconds: PositiveFloat = 2
    forget_seconds: PositiveFloat = 30


class FloorCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["virtual"] = "virtual"
    reference: str | None = None
    landmarks: list[dict[str, object]] = Field(default_factory=list)
    max_error: PositiveFloat = 0.05


class MultiCameraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cameras: list[CameraConfig] = Field(min_length=1, max_length=4)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    walkout: WalkoutConfig | None = None
    image_size: int = Field(default=416, ge=32, multiple_of=32)
    target_fps: PositiveFloat = 12
    fusion: FusionConfig = Field(default_factory=FusionConfig)
    calibration: FloorCalibration | None = None

    @property
    def active_cameras(self) -> list[CameraConfig]:
        return [camera for camera in self.cameras if camera.enabled]

    @model_validator(mode="after")
    def unique_cameras(self) -> Self:
        if not self.active_cameras:
            raise ValueError("Keep at least one camera enabled")
        if len({camera.id for camera in self.cameras}) != len(self.cameras):
            raise ValueError("Each camera needs a unique id")
        if any(camera.id == "grid" for camera in self.cameras):
            raise ValueError("Camera id 'grid' is reserved for the combined feed")
        if sum(camera.door_line is not None for camera in self.cameras) > 1:
            raise ValueError("Choose one camera to count doorway crossings")
        indexes = [int(str(camera.source)) for camera in self.active_cameras if str(camera.source).isdecimal()]
        if len(set(indexes)) != len(indexes):
            raise ValueError("Each live camera needs a different index")
        if any(isinstance(camera.source, int) and camera.source < 0 for camera in self.cameras):
            raise ValueError("Camera indexes must be non-negative")
        return self
