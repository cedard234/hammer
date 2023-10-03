#  hammer_tech.py
#  Python interface to the hammer technology abstraction.
#
#  See LICENSE for licence details.

import json
import shutil, os
import tarfile
import importlib
import importlib.resources
import subprocess
from abc import abstractmethod
from typing import Any, Callable, Iterable, List, Optional, Tuple, Dict, TYPE_CHECKING, Union
from numbers import Number
from decimal import Decimal
import warnings

from pydantic import BaseModel, Field

import hammer.config as hammer_config

from hammer.config import load_yaml, HammerJSONEncoder
from hammer.logging import HammerVLSILoggingContext
from hammer.utils import (LEFUtils, LIBUtils, add_lists, deeplist, get_or_else,
                          in_place_unique, optional_map, reduce_list_str,
                          reduce_named, coerce_to_grid)
from hammer.vlsi.units import TimeValue, CapacitanceValue

if TYPE_CHECKING:
    from hammer.vlsi.hooks import HammerToolHookAction

from .stackup import RoutingDirection, WidthSpacingTuple, Metal, Stackup
from .specialcells import CellType, SpecialCell


class Corner(BaseModel):
    nmos: str
    pmos: str
    temperature: str


class MinMaxCap(BaseModel):
    max_cap: str
    min_cap: str


class Provide(BaseModel):
    lib_type: str
    vt: Optional[str]


class Supplies(BaseModel):
    GND: str
    VDD: str


TLUPlusMapFile = str


class SpiceModelFile(BaseModel):
    # Struct that holds information about Spice model files.
    path: str
    lib_corner: str

    def to_setting(self) -> dict:
        output = {'path': str(self.path)}
        if self.lib_corner is not None:
            output.update({'lib corner': str(self.lib_corner)})
        return output

    @staticmethod
    def from_setting(d: dict) -> "SpiceModelFile":
        lib_corner = d['lib corner']
        if lib_corner is not None:
            lib_corner = str(d['lib corner'])
        return SpiceModelFile(
            path=str(d['path']),
            lib_corner=lib_corner
        )


class PathPrefix(BaseModel):
    """
    A path prefix which defines an identifier and its corresponding path.

    Example:
    A PathPrefix(id = "Alib", path = "/scratch/projectA/mylib") maps the identifier
        'Alib' to the path '/scratch/projectA/mylib'
    """
    id: str
    path: str

    def prepend(self, rest_of_path: str) -> str:
        """
        Prepend the path held by this PathPrefix to the given rest_of_path.
        :param rest_of_path: Rest of the path
        :return: Path held by this prefix prepended to rest_of_path.
        """
        return os.path.join(self.path, rest_of_path)


class Library(BaseModel):
    # TODO: refactor into library types, currently a Library is defined by just a small
    #   set of these fields (e.g. lef, gds, lib, verilog for stdcell libraries)
    name: Optional[str] = None
    ccs_liberty_file: Optional[str] = None
    ccs_library_file: Optional[str] = None
    ecsm_liberty_file: Optional[str] = None
    ecsm_library_file: Optional[str] = None
    corner: Optional[Corner] = None
    itf_files: Optional[MinMaxCap] = None
    lef_file: Optional[str] = None
    klayout_techfile: Optional[str] = None
    spice_file: Optional[str] = None
    gds_file: Optional[str] = None
    milkyway_lib_in_dir: Optional[str] = None
    milkyway_techfile: Optional[str] = None
    nldm_liberty_file: Optional[str] = None
    nldm_library_file: Optional[str] = None
    openaccess_techfile: Optional[str] = None
    provides: Optional[List[Provide]] = None
    qrc_techfile: Optional[str] = None
    supplies: Optional[Supplies] = None
    tluplus_files: Optional[MinMaxCap] = None
    tluplus_map_file: Optional[TLUPlusMapFile] = None
    verilog_sim: Optional[str] = None
    verilog_synth: Optional[str] = None
    spice_model_file: Optional[SpiceModelFile] = None
    power_grid_library: Optional[str] = None
    extra_prefixes: Optional[List[PathPrefix]] = None


PathsFunctionType = Callable[[Library], List[str]]
ExtractionFunctionType = Optional[Callable[[Library, List[str]], List[str]]]


class LibraryFilter(BaseModel):
    """
    "Library" filter containing a filtering function, identifier tag, and a short
    human-readable description.
    """
    tag: str
    description: str
    # Is the resulting string intended to be a file?
    is_file: bool
    # Function to extract desired path(s) out of the library.
    # Returns a list of library-relative paths.
    paths_func: PathsFunctionType
    # Function to extract desired string(s) out of the library, given full
    # paths and the Library.
    # Returns a list of strings.
    extraction_func: ExtractionFunctionType = None
    # Additional filter function to use to exclude possible libraries.
    filter_func: Optional[Callable[[Library], bool]] = None
    # Sort function to control the order in which outputs are listed
    sort_func: Optional[Callable[[Library], Union[Number, str, tuple]]] = None
    # List of functions to call on the list-level (the list of elements generated by func) before output and
    # post-processing.
    extra_post_filter_funcs: List[Callable[[List[str]], List[str]]] = []


Cell = str


class DRCDeck(BaseModel):
    tool_name: str
    deck_name: str
    path: str


class LVSDeck(BaseModel):
    tool_name: str
    deck_name: str
    path: str


class Tarball(BaseModel):
    root: PathPrefix
    homepage: str
    optional: bool = False


WidthTableEntry = Decimal


class Site(BaseModel):
    """
    A standard cell site, which is the minimum unit of x and y dimensions a standard cell can have.

    name: The name of this site (often something like "core") as defined in the tech and standard cell LEFs
    x: The x dimension
    y: The y dimension
    """
    name: str
    x: Decimal
    y: Decimal

    @staticmethod
    def from_setting(grid_unit: Decimal, d: Dict[str, Any]) -> "Site":
        """
        Return a new Site

        :param grid_unit: The manufacturing grid unit in nm
        :param d: A dictionary with the keys "name", "x", and "y"
        :return: A Site
        """
        return Site(
            name=str(d["name"]),
            x=coerce_to_grid(d["x"], grid_unit),
            y=coerce_to_grid(d["y"], grid_unit)
        )


class TechJSON(BaseModel):
    name: str
    grid_unit: Optional[str]
    shrink_factor: Optional[str]
    installs: Optional[List[PathPrefix]]
    libraries: Optional[List[Library]]
    gds_map_file: Optional[str]
    physical_only_cells_list: Optional[List[Cell]]
    dont_use_list: Optional[List[Cell]]
    drc_decks: Optional[List[DRCDeck]]
    lvs_decks: Optional[List[LVSDeck]]
    tarballs: Optional[List[Tarball]]
    sites: Optional[List[Site]]
    stackups: Optional[List[Stackup]]
    special_cells: Optional[List[SpecialCell]]
    extra_prefixes: Optional[List[PathPrefix]]
    additional_lvs_text: Optional[str]
    additional_drc_text: Optional[str]


def copy_library(lib: Library) -> Library:
    """Perform a deep copy of a Library."""
    return Library.parse_raw(lib.json())


def library_from_json(json: str) -> Library:
    """
    Creatre a library from a JSON string.
    :param json: JSON string.
    :return: hammer_tech library.
    """
    return Library.parse_raw(json)


# Struct that holds an extra library and possible prefix.
class ExtraLibrary(BaseModel):
    prefix: Optional[PathPrefix]
    library: Library

    def store_into_library(self) -> Library:
        """
        Store the prefix into extra_prefixes of the library, and return a new copy.
        :return: A copy of the library in this ExtraPrefix with the prefix stored in extra_prefixes, if one exists.
        """
        lib_copied: Library = copy_library(self.library)
        extra_prefixes: List[PathPrefix] = []
        if self.prefix:
            extra_prefixes.append(self.prefix)
        lib_copied.extra_prefixes = extra_prefixes
        return lib_copied


# Struct that holds information about the size of a macro.
# See defaults.yml.
class MacroSize(BaseModel):
    library: str
    name: str
    width: Decimal
    height: Decimal

    def to_setting(self) -> dict:
        return self.dict()

    @staticmethod
    def from_setting(d: dict) -> "MacroSize":
        return MacroSize(
            library=str(d['library']),
            name=str(d['name']),
            width=Decimal(str(d['width'])),
            height=Decimal(str(d['height']))
        )


class HammerTechnology:
    """
    Abstraction layer of Technology.
    This can be overridden by add `__init__.py` to a specific technology like `technology/asap7/__init__.py`
    """

    # Properties.
    @property
    def cache_dir(self) -> str:
        """
        Get the location of a cache dir for this library.

        :return: Path to the location of the cache dir.
        """
        try:
            return self._cachedir
        except AttributeError:
            raise ValueError("Internal error: cache dir location not set by hammer-vlsi")

    @cache_dir.setter
    def cache_dir(self, value: str) -> None:
        """Set the directory as a persistent cache dir for this library."""
        self._cachedir = value  # type: str
        # Ensure the cache_dir exists.
        os.makedirs(value, mode=0o700, exist_ok=True)

    # @classmethod
    def expand_tech_cache_path(self, path) -> str:
        """ Replace occurrences of the cache directory's basename with
            the full path to the cache dir."""
        return path.replace("cache", self.cache_dir)

    # @classmethod
    def ensure_dirs_exist(self, path) -> None:
        dir_name = os.path.dirname(path)
        if not os.path.exists(dir_name):
            self.logger.info('Creating directory: {}'.format(dir_name))
            os.makedirs(dir_name)

    # hammer-vlsi properties.
    # TODO: deduplicate/put these into an interface to share with HammerTool?
    @property
    def logger(self) -> HammerVLSILoggingContext:
        """Get the logger for this tool."""
        try:
            return self._logger
        except AttributeError:
            raise ValueError("Internal error: logger not set by hammer-vlsi")

    @logger.setter
    def logger(self, value: HammerVLSILoggingContext) -> None:
        """Set the logger for this tool."""
        self._logger = value  # type: HammerVLSILoggingContext

    # Methods.
    def __init__(self):
        """Don't call this directly. Use other constructors like load_from_module()."""
        # Name of the technology
        self.name: str = ""

        # The technology Python package
        self.package: str = ""

        # Configuration
        self.config: TechJSON = None

        # Units
        self.time_unit: Optional[TimeValue] = None
        self.cap_unit: Optional[CapacitanceValue] = None

    @classmethod
    def load_from_module(cls, tech_module: str) -> Optional["HammerTechnology"]:
        """Load a technology from a given module.

        :param tech_module: Technology module (e.g. "hammer.technology.asap7")
        :return: Loaded technology plugin or None if the package did not have an appropriate tech.json/tech.yaml
        """
        technology_name = tech_module.split('.')[-1]
        mod = importlib.import_module(tech_module)
        tech: HammerTechnology = mod.tech
        tech.name = technology_name
        tech.package = tech_module

        tech_json = importlib.resources.files(tech_module) / f"{technology_name}.tech.json"
        tech_yaml = importlib.resources.files(tech_module) / f"{technology_name}.tech.yml"

        if tech_json.is_file():
            tech.config = TechJSON.parse_raw(tech_json.read_text())
            return tech
        elif tech_yaml.is_file():
            tech.config = TechJSON.parse_raw(json.dumps(load_yaml(tech_yaml.read_text())))
            return tech
        else: #TODO - from Pydantic model instance
            return None

    def get_lib_units(self) -> None:
        """
        Get time and capacitance units from the first LIB file
        Must be called right after the tech module is loaded.
        """
        libs = self.read_libs(
                [filters.get_timing_lib_with_preference("NLDM")],
                HammerTechnologyUtils.to_plain_item)
        if len(libs) > 0:
            tu = LIBUtils.get_time_unit(libs[0])
            cu = LIBUtils.get_cap_unit(libs[0])
            if tu is None:
                self.logger.error("Error in parsing first NLDM Liberty file for time units.")
            else:
                self.time_unit = TimeValue(tu)
            if cu is None:
                self.logger.error("Error in parsing first NLDM Liberty file for capacitance units.")
            else:
                self.cap_unit = CapacitanceValue(cu)
        else:
            self.logger.error("No NLDM libs defined. Time/cap units will be defined by the tool or another technology.")

    def set_database(self, database: hammer_config.HammerDatabase) -> None:
        """Set the settings database for use by the tool."""
        self._database = database  # type: hammer_config.HammerDatabase

    def is_database_set(self) -> bool:
        """Return True if the settings database has been set for use by the tool."""
        return hasattr(self, "_database")

    def get_setting(self, key: str) -> Any:
        """Get a particular setting from the database.
        """
        try:
            return self._database.get(key)
        except AttributeError:
            raise ValueError("Internal error: no database set by hammer-vlsi")
        except KeyError as e:  # this function is expected to return Optional[str] from extracted_tarballs_dir()
            print(e)  # TODO: fix the root cause
            return None

    def get_setting_suffix(self, key: str) -> Any:
        """Get a particular setting from the database with a suffix.
        """
        try:
            return self._database.get(key)
        except AttributeError:
            raise ValueError("Internal error: no database set by hammer-vlsi")
        except KeyError as e:  # this function is expected to return Optional[str] from extracted_tarballs_dir()
            print(e)  # TODO: fix the root cause
            return None

    def has_setting(self, key: str) -> bool:
        """Check if a setting exists in the database.
        """
        return self._database.has_setting(key)

    def get_config(self) -> Tuple[List[dict], List[dict]]:
        """Get the hammer configuration for this technology. Not to be confused with the ".tech.json" which
        self.config refers to. """
        return hammer_config.load_config_from_defaults(self.package, types=True)

    @property
    def dont_use_list(self) -> Optional[List[str]]:
        """
        Get the list of blacklisted ("don't use") cells.
        :return: List of "don't use" cells, or None if the technology does not define such a list.
        """
        dont_use_list_raw = self.config.dont_use_list  # type: Optional[List[str]]
        if dont_use_list_raw is None:
            return None
        else:
            # Work around the weird objects implemented by the jsonschema generator.
            dont_use_list = list(map(str, list(dont_use_list_raw)))
            return dont_use_list

    @property
    def physical_only_cells_list(self) -> Optional[List[str]]:
        """
        Get the list of physical only cells.
        :return: List of physical only cells, or None if the technology does not define such a list.
        """
        physical_only_cells_list_raw = self.config.physical_only_cells_list  # type: Optional[List[str]]
        if physical_only_cells_list_raw is None:
            return None
        else:
            # Work around the weird objects implemented by the jsonschema generator.
            physical_only_cells_list = [str(x) for x in physical_only_cells_list_raw]
            return physical_only_cells_list

    @property
    def additional_drc_text(self) -> str:
        add_drc_text_raw = self.config.additional_drc_text
        if add_drc_text_raw is None:
            return ""
        else:
            return str(add_drc_text_raw)

    @property
    def additional_lvs_text(self) -> str:
        add_lvs_text_raw = self.config.additional_lvs_text
        if add_lvs_text_raw is None:
            return ""
        else:
            return str(add_lvs_text_raw)

    def get_lvs_decks_for_tool(self, tool_name: str) -> List[LVSDeck]:
        """
        Return the LVS decks for the given tool.
        """
        if self.config.lvs_decks is not None:
            for deck in self.config.lvs_decks:
                deck.path = self.prepend_dir_path(deck.path)
            return [x for x in self.config.lvs_decks if x.tool_name == tool_name]
        else:
            raise ValueError("Tech JSON does not specify any LVS decks")

    def get_drc_decks_for_tool(self, tool_name: str) -> List[DRCDeck]:
        """
        Return the DRC decks for the given tool.
        """
        if self.config.drc_decks is not None:
            for deck in self.config.drc_decks:
                deck.path = self.prepend_dir_path(deck.path)
            return [x for x in self.config.drc_decks if x.tool_name == tool_name]
        else:
            raise ValueError("Tech JSON does not specify any DRC decks")

    @property
    def extracted_tarballs_dir(self) -> str:
        """
        Return the path to a folder with extracted tarballs.
        If no pre-extracted dir is specified, then it will be under
        self.path.
        See defaults.yml.
        """
        tech_setting_key = "technology.{name}.extracted_tarballs_dir".format(name=self.name)
        if self.has_setting(tech_setting_key):
            tech_setting = self.get_setting(tech_setting_key)  # type: Optional[str]
            if tech_setting is not None:
                return tech_setting

        # No tech setting
        extracted_tarballs_dir_setting = self.get_setting(
            "vlsi.technology.extracted_tarballs_dir")  # type: Optional[str]
        if extracted_tarballs_dir_setting is None:
            return os.path.join(self.cache_dir, "extracted")
        else:
            return extracted_tarballs_dir_setting

    @staticmethod
    def parse_library(lib: dict) -> Library:
        """
        Parse a given lib in dictionary form to a hammer_tech Library (IP library).
        :param lib: Library to parse, must be a dictionary
        :return: Parsed hammer_tech Library or exception.
        """
        if not isinstance(lib, dict):
            raise TypeError("lib must be a dict")

        # Convert the dict to JSON...
        return Library.parse_raw(json.dumps(lib, cls=HammerJSONEncoder))

    @property
    def tech_defined_libraries(self) -> List[Library]:
        """
        Get all technology-defined libraries from the config.
        :return: List of technology-defined libraries with any extra prefixes if present.
        """
        if self.config.libraries:
            return self.config.libraries
        else:
            return []

    def get_extra_macro_sizes(self) -> List[MacroSize]:
        """
        Get the list of extra macro sizes from the config.
        See vlsi.technology.extra_macro_sizes in defaults.yml.
        :return: List of extra macro sizes.
        """
        if not self.has_setting("vlsi.technology.extra_macro_sizes"):
            # If the key doesn't exist we can safely say there are none.
            return []

        extra_macro_sizes = self.get_setting("vlsi.technology.extra_macro_sizes")
        if not isinstance(extra_macro_sizes, list):
            raise ValueError("extra_macro_sizes was not a list")
        else:
            return list(map(MacroSize.from_setting, extra_macro_sizes))

    def get_tech_macro_sizes(self) -> List[MacroSize]:
        """
        Compile a list of all macros which have size information, using LEF files.
        This also considers any extra IP libraries.
        :return: List of all macros' size information.
        """

        # Enhance lef_filter to also extract the name of the library.
        def extraction_func(lib: Library, paths: List[str]) -> List[str]:
            assert len(paths) == 1, "paths_func above returns only one item"
            # For type checker
            lib_name = lib.name
            if lib_name is None:
                name = ""
            else:
                name = str(lib_name)
            return [json.dumps([paths[0], name], cls=HammerJSONEncoder)]

        lef_filter_plus = filters.lef_filter.copy(deep=True)
        lef_filter_plus.extraction_func = extraction_func

        lef_names_filenames_serialized = self.process_library_filter(filt=lef_filter_plus,
                                                                     pre_filts=self.default_pre_filters(),
                                                                     output_func=HammerTechnologyUtils.to_plain_item,
                                                                     must_exist=True)

        result = []  # type: List[MacroSize]

        for serialized in lef_names_filenames_serialized:
            lef_filename, name = json.loads(serialized)
            with open(lef_filename, 'r') as f:
                lef_file_contents = str(f.read())
            sizes = LEFUtils.get_sizes(lef_file_contents)
            if len(sizes) == 0:
                continue

            if name == "":
                self.logger.warning(
                    "No name is set for the library containing {lef_filename}".format(lef_filename=lef_filename))

            for s in sizes:
                result.append(MacroSize(
                    library=name,
                    name=s[0],
                    width=s[1],
                    height=s[2]
                ))

        return result

    def get_macro_sizes(self) -> List[MacroSize]:
        """
        Get the list of all macro blocks' sizes for export to other tools.
        :return: List of all macro sizes.
        """
        return self.get_extra_macro_sizes() + self.get_tech_macro_sizes()

    def prepend_dir_path(self, path: str, lib: Optional[Library] = None) -> str:
        """
        :param path: Path to which we should prepend a path prefix according to the path's prefix identifier
        :param lib: (optional) Library which produced this path. Used to look for additional prefixes.

        A tech JSON contains a description of the installs and tarballs that contain the technology files.
        It can also contain Libraries.

        "installs": [{
            "root": {
                "id": "pdkroot",
                "path": "/nfs/ecad/tsmc100/stdcells/"
            }
        }],
        "libraries": [{
            "name": "lib1",
            "extra_prefixes": [
                {"id": "lib1", "path": "/design_files/caps/"}
            ]
        }]

        "installs.root.id" is an identifier (prefix) and "installs.root.path" is the actual path to the install directory.

        The "path" passed into this function can be one of five types:

        1. Absolute path: the path starts with "/" and refers to an absolute path on the filesystem
            /path/to/a/lib/file.lib -> /path/to/a/lib/file.lib
        2. Tech plugin relative path: the path has no "/"s and refers to a file directly inside the tech plugin folder
            techlib.lib -> <tech plugin package>/techlib.lib
        3. Tech cache relative path: the path starts with an identifier which is "cache" (this is used in the SKY130 tech JSON)
            cache/primitives.v -> <tech plugin cache dir>/primitives.v
        4. Install relative path: the path starts with an install/tarball identifier (installs.id, tarballs.root.id)
        and refers to a file relative to that identifier's path
            pdkroot/dac/dac.lib -> /nfs/ecad/tsmc100/stdcells/dac/dac.lib
        5. Library extra_prefix path: the path starts with an identifier present in the provided
            library's extra_prefixes
            lib1/cap150f.lib -> /design_files/caps/cap150f.lib
        """
        assert len(path) > 0, "path must not be empty"

        # 1. If the path is an absolute path, return it as-is.
        if os.path.isabs(path):
            return path

        # 2. If the path has no path separator, the path is relative to the tech plugin package itself.
        #    Do not need to copy the resource into the cache dir because poetry packages all resources into site-packages.
        if os.sep not in path:
            resource_path = importlib.resources.files(self.package) / path
            assert resource_path.is_file(),\
                f"{path} wasn't found in HammerTechnology Python package {self.package}"
            return str(resource_path)

        # 3-5. The path consists of an identifier (prefix) and the rest of the path now
        id = path.split(os.path.sep)[0]
        rest_of_path = path.split(os.path.sep)[1:]

        # 3. If the path id is "cache", the path is relative to the cache dir
        if id == "cache":
            return os.path.join(self.cache_dir, *rest_of_path)

        # 4-5. Search the installs, tarballs, and Library's extra_prefixes to find any matching identifier
        prefixes: List[PathPrefix] = []
        if self.config.installs:
            prefixes.extend([  # installs.path is a reference to a DB key set in a project yml
                PathPrefix(id=id, path=self.get_setting(pp.path)) for pp in self.config.installs
                if pp.id == id])
        if self.config.tarballs:
            prefixes.extend([  # use the extracted tarballs dir instead of the tarball path itself
                PathPrefix(id=id, path=os.path.join(self.extracted_tarballs_dir, pp.root.id)) for pp in self.config.tarballs
                if pp.root.id == id])
        if lib and lib.extra_prefixes:  # TODO: not sure if the library path needs variable substitution
            prefixes.extend([pp for pp in lib.extra_prefixes if pp.id == id])

        if len(prefixes) < 1:
            raise ValueError(f"Path {path} with prefix id {id} did not match any tarballs or installs")
        if len(prefixes) > 1:
            raise ValueError(f"Path {path} with prefix id {id} matched more than one tarball or install: {prefixes}")

        assert len(prefixes) == 1
        return prefixes[0].prepend(os.path.join(*rest_of_path))

    def extract_technology_files(self) -> None:
        """Ensure that the technology files exist via tarballs and/or installs."""
        if self.config.installs is None and self.config.tarballs is None:
            raise ValueError("Technology specified neither tarballs nor installs")
        else:
            if self.config.installs is not None:
                self.check_installs()
            if self.config.tarballs is not None:
                self.extract_tarballs()
            self.post_install_script()

    def check_installs(self) -> bool:
        """Check that the all directories for a pre-installed technology actually exist.

        :return: Return True if the directories is OK, False otherwise."""
        if not self.config.installs:
            return False
        for install in self.config.installs:
            # This is a key to look up (the user yml will set this key to the actual PDK install path)
            path_key = install.path
            install_path = str(self.get_setting(path_key))
            if not os.path.exists(install_path):
                self.logger.error(f"The install path: {install_path} does not exist, looked at key {path_key}")
                return False
        return True

    def extract_tarballs(self) -> None:
        """Extract tarballs to the given cache_dir, or verify that they've been extracted."""
        if not self.config.tarballs:
            return
        for tarball in self.config.tarballs:
            target_path = os.path.join(self.extracted_tarballs_dir, tarball.root.id)
            tarball_path = os.path.join(self.get_setting(tarball.root.path), tarball.root.id)
            if not os.path.isfile(tarball_path):
                if tarball.optional:
                    continue
                else:
                    raise ValueError("Path {0} does not point to a valid tarball!".format(tarball_path))
            if os.path.isdir(target_path):
                # If the folder already seems to exist, continue
                continue
            else:
                # Else, extract the tarballs.
                os.makedirs(target_path, mode=0o700, exist_ok=True)  # Make sure it exists or tar will not be happy.
                self.logger.debug("Extracting/verifying tarball %s" % tarball_path)
                tarfile.open(tarball_path).extractall(target_path)
                for root, dirs, files in os.walk(target_path):
                    for d in dirs:
                        os.chmod(os.path.join(root, d), mode=0o700)
                    for f in files:
                        file = os.path.join(root, f)
                        os.chmod(file, mode=0o700)
                        # extract tarball recursively
                        if tarfile.is_tarfile(file):
                            self.logger.debug("Extracting/verifying tarball %s" % file)
                            tarfile.open(file).extractall(path=os.path.join(root, f + "_dir"))
                            os.remove(file)
                            os.renames(os.path.join(root, f + "_dir"), file)

    def post_install_script(self) -> None:
        """a script to apply any needed hotfixes to technology libraries, tech __init__.py will override this"""
        pass

    def get_extra_libraries(self) -> List[ExtraLibrary]:
        """
        Get the list of extra libraries from the config.
        See vlsi.technology.extra_libraries in defaults.yml.
        :return: List of extra libraries.
        """
        if not self.has_setting("vlsi.technology.extra_libraries"):
            # If the key doesn't exist we can safely say there are no extra libraries.
            return []

        extra_libs = self.get_setting("vlsi.technology.extra_libraries")
        if not isinstance(extra_libs, list):
            raise ValueError("extra_libraries was not a list")
        else:
            return [ExtraLibrary.parse_obj(lib) for lib in extra_libs]

    def get_available_libraries(self) -> List[Library]:
        """
        Get all available IP libraries. Currently this consists of IP libraries from the technology as well as
        extra IP libraries specified in the config (see get_extra_libraries).
        :return: List of all available IP libraries.
        """
        return list(self.tech_defined_libraries) + list(
            map(lambda el: el.store_into_library(), self.get_extra_libraries()))

    def process_library_filter(self,
                               filt: LibraryFilter,
                               pre_filts: List[Callable[[Library], bool]],
                               output_func: Callable[[str, LibraryFilter], List[str]],
                               must_exist: bool = True,
                               uniquify: bool = True) -> List[str]:
        """
        Process the given library filter and return a list of items from that library filter with any extra
        post-processing.

        - Get a list of lib items
        - Run any extra_post_filter_funcs (if needed)
        - For every lib item in each lib items, run output_func

        :param filt: LibraryFilter to check against all libraries.
        :param pre_filts: List of functions with which to pre-filter the libraries. Each function must return true
                          in order for this library to be used.
        :param output_func: Function which processes the outputs, taking in the filtered lib and the library filter
                            which generated it.
        :param must_exist: Must each library item actually exist? Default: True (yes, they must exist)
        :param uniquify: Must uniqify the list of output files. Default: True
        :return: Resultant items from the filter and post-processed. (e.g. --timing foo.db --timing bar.db)
        """

        # First, filter the list of available libraries with pre_filts and the library itself.
        lib_filters = pre_filts + get_or_else(optional_map(filt.filter_func, lambda x: [x]), [])

        filtered_libs = list(reduce_named(
            sequence=lib_filters,
            initial=self.get_available_libraries(),
            function=lambda libs, func: filter(func, libs)
        ))  # type: List[Library]

        # Next, sort the list of libraries if a sort function exists.
        if filt.sort_func is not None:
            # Possible mypy quirk
            filtered_libs = sorted(filtered_libs, key=filt.sort_func)  # type: ignore

        # Next, extract paths and prepend them to get the real paths.
        def get_and_prepend_path(lib: Library) -> Tuple[Library, List[str]]:
            paths = filt.paths_func(lib)
            full_paths = list(map(lambda path: self.prepend_dir_path(path, lib), paths))
            return lib, full_paths

        libs_and_paths = list(map(get_and_prepend_path, filtered_libs))  # type: List[Tuple[Library, List[str]]]

        # Existence checks for paths.
        def check_lib_and_paths(inp: Tuple[Library, List[str]]) -> Tuple[Library, List[str]]:
            lib = inp[0]  # type: Library
            paths = inp[1]  # type: List[str]
            existence_check_func = self.make_check_isfile(filt.description) if filt.is_file else self.make_check_isdir(
                filt.description)
            paths = list(map(existence_check_func, paths))
            return lib, paths

        if must_exist:
            libs_and_paths = list(map(check_lib_and_paths, libs_and_paths))

        # Now call the extraction function to get a final list of strings.

        # If no extraction function was specified, use the identity extraction
        # function.
        def identity_extraction_func(lib: "Library", paths: List[str]) -> List[str]:
            return paths

        extraction_func = get_or_else(filt.extraction_func, identity_extraction_func)

        output_list = reduce_list_str(add_lists, list(map(lambda t: extraction_func(t[0], t[1]), libs_and_paths)),
                                      [])  # type: List[str]

        # Quickly check that it is actually a List[str].
        if not isinstance(output_list, List):
            raise TypeError("output_list is not a List[str], but a " + str(type(output_list)))
        for i in output_list:
            if not isinstance(i, str):
                raise TypeError("output_list is a List but not a List[str]")

        # Uniquify results.
        # TODO: think about whether this really belongs here and whether we always need to uniquify.
        # This is here to get stuff working since some CAD tools dislike duplicated arguments (e.g. duplicated stdcell
        # lib, etc).
        if uniquify:
            in_place_unique(output_list)

        # Apply any list-level functions.
        after_post_filter = reduce_named(
            sequence=filt.extra_post_filter_funcs,
            initial=output_list,
            function=lambda libs, func: func(list(libs)),
        )

        # Finally, apply any output functions.
        # e.g. turning foo.db into ["--timing", "foo.db"].
        after_output_functions = list(map(lambda item: output_func(item, filt), after_post_filter))

        # Concatenate lists of List[str] together.
        return reduce_list_str(add_lists, after_output_functions, [])

    def read_libs(self, library_types: Iterable[LibraryFilter], output_func: Callable[[str, LibraryFilter], List[str]],
                  extra_pre_filters: Optional[List[Callable[[Library], bool]]] = None,
                  must_exist: bool = True) -> List[str]:
        """
        Read the given libraries and return a list of strings according to some output format.

        :param library_types: List of libraries to filter, specified as a list of LibraryFilter elements.
        :param output_func: Function which processes the outputs, taking in the filtered lib and the library filter
                            which generated it.
        :param extra_pre_filters: List of additional filter functions to use to filter the list of libraries.
        :param must_exist: Must each library item actually exist? Default: True (yes, they must exist)
        :return: List of filtered libraries processed according output_func.
        """

        pre_filts = self.default_pre_filters()  # type: List[Callable[[Library], bool]]
        if extra_pre_filters is not None:
            assert isinstance(extra_pre_filters, List)
            pre_filts += extra_pre_filters

        return reduce_list_str(
            add_lists,
            map(
                lambda lib: self.process_library_filter(pre_filts=pre_filts, filt=lib, output_func=output_func,
                                                        must_exist=must_exist),
                library_types
            )
        )



    def default_pre_filters(self) -> List[Callable[[Library], bool]]:
        """
        Get the list of default pre-filters to pre-filter out IP libraries
        before processing a LibraryFilter.
        """
        return [self.filter_for_supplies]

    def filter_for_supplies(self, lib: Library) -> bool:
        """Function to help filter a list of libraries to find libraries which have matching supplies.
        Will also use libraries with no supplies annotation.

        :param lib: Library to check
        :return: True if the supplies of this library match the inputs for this run, False otherwise.
        """
        # If we are using MMMC assume all libraries will be used.
        # TODO: Read the corners and filter out libraries that don't match any of them.
        # Requires a refactor because MMMCCorner parsing is only in HammerTool now.
        # See issue #275.
        if self.get_setting("vlsi.inputs.mmmc_corners"):
            return True
        if lib.supplies is None:
            # TODO: add some sort of wildcard value for supplies for libraries which _actually_ should
            # always be used.
            if lib.provides is not None:
                for provided in lib.provides:
                    if provided.lib_type is not None and provided.lib_type == "technology":
                        return True
            self.logger.warning("Lib %s has no supplies annotation! Using anyway." % (lib.json()))
            return True
        return self.get_setting("vlsi.inputs.supplies.VDD") == lib.supplies.VDD and self.get_setting(
            "vlsi.inputs.supplies.GND") == lib.supplies.GND

    @staticmethod
    def make_check_isdir(description: str = "Path") -> Callable[[str], str]:
        """
        Utility function to generate functions which check whether a path exists.
        """

        def check_isdir(path: str) -> str:
            if not os.path.isdir(path):
                raise ValueError("%s %s is not a directory or does not exist" % (description, path))
            else:
                return path

        return check_isdir

    @staticmethod
    def make_check_isfile(description: str = "File") -> Callable[[str], str]:
        """
        Utility function to generate functions which check whether a path exists.
        """

        def check_isfile(path: str) -> str:
            if not os.path.isfile(path):
                raise ValueError("%s %s is not a file or does not exist" % (description, path))
            else:
                return path

        return check_isfile

    def get_stackup_by_name(self, name: str) -> Stackup:
        """
        Return the stackup details for the given key.
        """
        if self.config.stackups is not None:
            for stackup in self.config.stackups:
                if stackup.name == name:
                    return stackup
            raise ValueError("Stackup named %s is not defined in tech JSON" % name)
        else:
            raise ValueError("Tech JSON does not specify any stackups")

    def get_shrink_factor(self) -> Decimal:
        """
        Return the manufacturing shrink factor.
        """
        if self.config.shrink_factor is not None:
            return Decimal(self.config.shrink_factor)
        else:
            # TODO(johnwright) Warn the user that we are using a default shrink factor (they should update their tech plugin)
            return Decimal(1)

    def get_post_shrink_length(self, length: Decimal) -> Decimal:
        """
        Convert a drawn dimension into a manufactured (post-shrink) dimension.

        :param length: The drawn length
        :return: The post-shrink length
        """
        # TODO(ucb-bar/hammer#378) use hammer units for length and area
        return self.get_shrink_factor() * length

    def get_special_cell_by_type(self, cell_type: CellType) -> List[SpecialCell]:
        if self.config.special_cells is not None:
            return [fc for fc in self.config.special_cells if fc.cell_type == cell_type]
        else:
            return []

    def get_grid_unit(self) -> Decimal:
        """
        Return the manufacturing grid unit.
        """
        if self.config.grid_unit is not None:
            return Decimal(self.config.grid_unit)
        else:
            raise ValueError("Tech JSON does not specify a manufacturing grid unit")

    def get_site_by_name(self, name: str) -> Site:
        """
        Return the site for the given key.
        """
        if self.config.sites is not None:
            for item in self.config.sites:
                if item.name == name:
                    return item
            raise ValueError("Site named %s is not defined in tech JSON" % name)
        else:
            raise ValueError("Tech JSON does not specify any sites")

    def get_placement_site(self) -> Site:
        """
        Return the default placement site defined by the hammer setting "vlsi.technology.placement_site"
        """
        return self.get_site_by_name(self.get_setting("vlsi.technology.placement_site"))

    def get_tech_syn_hooks(self, tool_name: str) -> List['HammerToolHookAction']:
        """
        Return a list of synthesis hooks for this technology and tool.
        To be overridden by subclasses.
        """
        return list()

    def get_tech_par_hooks(self, tool_name: str) -> List['HammerToolHookAction']:
        """
        Return a list of place and route hooks for this technology and tool.
        To be overridden by subclasses.
        """
        return list()

    def get_tech_drc_hooks(self, tool_name: str) -> List['HammerToolHookAction']:
        """
        Return a list of DRC hooks for this technology and tool.
        To be overridden by subclasses.
        """
        return list()

    def get_tech_lvs_hooks(self, tool_name: str) -> List['HammerToolHookAction']:
        """
        Return a list of LVS hooks for this technology and tool.
        To be overridden by subclasses.
        """
        return list()

    def get_tech_sram_generator_hooks(self, tool_name: str) -> List['HammerToolHookAction']:
        """
        Return a list of sram generator hooks for this technology and tool.
        To be overridden by subclasses.
        """
        return list()

    def get_tech_sim_hooks(self, tool_name: str) -> List['HammerToolHookAction']:
        """
        Return a list of sim hooks for this technology and tool.
        To be overridden by subclasses.
        """
        return list()

    def get_tech_power_hooks(self, tool_name: str) -> List['HammerToolHookAction']:
        """
        Return a list of power hooks for this technology and tool.
        To be overridden by subclasses.
        """
        return list()

    def get_tech_formal_hooks(self, tool_name: str) -> List['HammerToolHookAction']:
        """
        Return a list of formal hooks for this technology and tool.
        To be overridden by subclasses.
        """
        return list()

    def get_tech_timing_hooks(self, tool_name: str) -> List['HammerToolHookAction']:
        """
        Return a list of timing hooks for this technology and tool.
        To be overridden by subclasses.
        """
        return list()

    def get_tech_pcb_hooks(self, tool_name: str) -> List['HammerToolHookAction']:
        """
        Return a list of pcb hooks for this technology and tool.
        To be overridden by subclasses.
        """
        return list()

    def extract_gz_files(self,extract_list:List[str]) -> List[str]:
        """
        Return list of paths of unzipped files (*.gz)
        Unzips the *.gz files to the cache directory.
        Files not ending in *.gz are unchanged and paths are included in the returned list.
        """
        dest_path = os.path.join(self._cachedir, "extracted_tarfiles")
        full_list = []

        try:
            os.makedirs(dest_path,mode=0o700, exist_ok=True)
        except:
            pass

        for tar_file in extract_list:
            if (tar_file.endswith('.gz')):
                if (os.path.splitext(os.path.basename((tar_file)))[0] not in os.listdir(dest_path)):
                    shutil.copy(tar_file, dest_path)
            else:
                full_list.append(tar_file)

        full_paths = [os.path.join(dest_path, os.path.basename(l)) for l in os.listdir(dest_path) if l.endswith('.gz')]
        for _path in full_paths:
            subprocess.call([f"gzip -d {_path}"], shell=True)

        full_list += [os.path.join(dest_path, os.path.basename(l)) for l in os.listdir(dest_path)]

        return full_list

class HammerTechnologyUtils:
    """
    Utility/helper functions for HammerTechnology.
    """

    @staticmethod
    def to_command_line_args(lib_item: str, filt: LibraryFilter) -> List[str]:
        """
        Generate command-line args in the form --<filt.tag> <lib_item>.
        """
        return ["--" + filt.tag, lib_item]

    @staticmethod
    def to_plain_item(lib_item: str, filt: LibraryFilter) -> List[str]:
        """
        Generate plain outputs in the form of <lib_item1> <lib_item2> ...
        """
        return [lib_item]


#  A collection of pre-implemented LibraryFilters.
class LibraryFilterHolder:
    """
    Dummy class to hold the list of properties.
    Instantiated by hammer_tech to be exposed as hammer_tech.filters.lef_filter etc.
    """

    @staticmethod
    def create_nonempty_check(description: str) -> Callable[[List[str]], List[str]]:
        """
        Create a function that checks that the list it is given has >1 element.
        :param description: Description to show in the error message.
        :return: Function that takes in the list of elements and returns a checked/processed version of itself.
        """
        def check_nonempty(l: List[str]) -> List[str]:
            if len(l) == 0:
                raise ValueError("Must have at least one " + description)
            else:
                return l

        return check_nonempty

    @property
    def timing_db_filter(self) -> LibraryFilter:
        """
        Selecting Synopsys timing libraries (.db). Prefers CCS if available; picks NLDM as a fallback.
        """

        def paths_func(lib: Library) -> List[str]:
            # Choose ccs if available, if not, nldm.
            if lib.ccs_library_file is not None:
                return [lib.ccs_library_file]
            elif lib.nldm_library_file is not None:
                return [lib.nldm_library_file]
            else:
                return []

        return LibraryFilter(
            tag="timing_db",
            description="CCS/NLDM timing lib (Synopsys .db)",
            paths_func=paths_func,
            is_file=True
        )

    @property
    def liberty_lib_filter(self) -> LibraryFilter:
        """
        Select ASCII liberty (.lib) timing libraries. Prefers CCS if available; picks NLDM as a fallback.
        """
        warnings.warn("Use timing_lib_filter instead", DeprecationWarning, stacklevel=2)

        def paths_func(lib: Library) -> List[str]:
            # Choose ccs if available, if not, nldm.
            if lib.ccs_liberty_file is not None:
                return [lib.ccs_liberty_file]
            elif lib.nldm_liberty_file is not None:
                return [lib.nldm_liberty_file]
            else:
                return []

        return LibraryFilter(
            tag="timing_lib",
            description="CCS/NLDM timing lib (ASCII .lib)",
            paths_func=paths_func,
            is_file=True
        )

    @property
    def timing_lib_filter(self) -> LibraryFilter:
        """
        Select ASCII .lib timing libraries. Prefers CCS if available; picks NLDM as a fallback.
        """

        def paths_func(lib: Library) -> List[str]:
            # Choose ccs if available, if not, nldm.
            if lib.ccs_liberty_file is not None:
                return [lib.ccs_liberty_file]
            elif lib.nldm_liberty_file is not None:
                return [lib.nldm_liberty_file]
            else:
                return []

        return LibraryFilter(
            tag="timing_lib",
            description="CCS/NLDM timing lib (ASCII .lib)",
            paths_func=paths_func,
            is_file=True
        )

    @property
    def timing_lib_with_ecsm_filter(self) -> LibraryFilter:
        """
        Select ASCII .lib timing libraries. Prefers ECSM, then CCS, then NLDM if multiple are present for
        a single given .lib.
        """

        def paths_func(lib: Library) -> List[str]:
            if lib.ecsm_liberty_file is not None:
                return [lib.ecsm_liberty_file]
            elif lib.ccs_liberty_file is not None:
                return [lib.ccs_liberty_file]
            elif lib.nldm_liberty_file is not None:
                return [lib.nldm_liberty_file]
            else:
                return []

        return LibraryFilter(
            tag="timing_lib_with_ecsm",
            description="ECSM/CCS/NLDM timing lib (liberty ASCII .lib)",
            paths_func=paths_func,
            is_file=True
        )

    def get_timing_lib_with_preference(self, lib_pref: str = "NLDM") -> LibraryFilter:
        """
        Select ASCII .lib timing libraries. Prefers NLDM, then ECSM, then CCS if multiple are present for
        a single given .lib.
        """
        lib_pref = lib_pref.upper()

        def paths_func(lib: Library) -> List[str]:
            pref_list = ["NLDM", "ECSM", "CCS"]
            index = None

            try:
                index = pref_list.index(lib_pref)
            except:
                raise ValueError("Library preference must be one of NLDM, ECSM, or CCS.")
            pref_list.insert(0, pref_list.pop(index))

            for elem in pref_list:
                if elem == "NLDM":
                    if lib.nldm_liberty_file is not None:
                        return [lib.nldm_liberty_file]
                elif elem == "ECSM":
                    if lib.ecsm_liberty_file is not None:
                        return [lib.ecsm_liberty_file]
                elif elem == "CCS":
                    if lib.ccs_liberty_file is not None:
                        return [lib.ccs_liberty_file]
                else:
                    pass

            return []

        return LibraryFilter(
            tag="timing_lib_with_nldm",
            description="ECSM/CCS/NLDM timing lib (liberty ASCII .lib)",
            paths_func=paths_func,
            is_file=True
        )

    @property
    def qrc_tech_filter(self) -> LibraryFilter:
        """
        Selecting qrc RC Corner tech (qrcTech) files.
        """

        def paths_func(lib: Library) -> List[str]:
            if lib.qrc_techfile is not None:
                return [lib.qrc_techfile]
            else:
                return []

        return LibraryFilter(
            tag="qrc",
            description="qrc RC corner tech file",
            paths_func=paths_func,
            is_file=True
        )

    @property
    def verilog_synth_filter(self) -> LibraryFilter:
        """
        Selecting verilog_synth files which are synthesizable wrappers (e.g. for SRAM) which are needed in some
        technologies.
        """

        def paths_func(lib: Library) -> List[str]:
            if lib.verilog_synth is not None:
                return [lib.verilog_synth]
            else:
                return []

        return LibraryFilter(
            tag="verilog_synth",
            description="Synthesizable Verilog wrappers",
            paths_func=paths_func,
            is_file=True
        )

    @property
    def lef_filter(self) -> LibraryFilter:
        """
        Select LEF files for physical layout.
        """

        def filter_func(lib: Library) -> bool:
            return lib.lef_file is not None

        def paths_func(lib: Library) -> List[str]:
            assert lib.lef_file is not None
            return [lib.lef_file]

        def sort_func(lib: Library):
            if lib.provides is not None:
                for provided in lib.provides:
                    if provided.lib_type is not None and provided.lib_type == "technology":
                        return 0  # put the technology LEF in front
            return 100  # put it behind

        return LibraryFilter(
            tag="lef",
            description="LEF physical design layout library",
            is_file=True,
            filter_func=filter_func,
            paths_func=paths_func,
            sort_func=sort_func
        )

    @property
    def verilog_sim_filter(self) -> LibraryFilter:
        """
        Select verilog sim files for gate level simulation
        """

        def filter_func(lib: Library) -> bool:
            return lib.verilog_sim is not None

        def paths_func(lib: Library) -> List[str]:
            assert lib.verilog_sim is not None
            return [lib.verilog_sim]

        return LibraryFilter(
            tag="verilog_sim",
            description="Gate-level verilog sources",
            is_file=True,
            filter_func=filter_func,
            paths_func=paths_func
        )

    @property
    def gds_filter(self) -> LibraryFilter:
        """
        Select GDS files for opaque physical information.
        """

        def filter_func(lib: Library) -> bool:
            return lib.gds_file is not None

        def paths_func(lib: Library) -> List[str]:
            assert lib.gds_file is not None
            return [lib.gds_file]

        return LibraryFilter(
            tag="gds",
            description="GDS opaque physical design layout",
            is_file=True,
            filter_func=filter_func,
            paths_func=paths_func
        )

    @property
    def spice_filter(self) -> LibraryFilter:
        """
        Select SPICE files.
        """

        def filter_func(lib: Library) -> bool:
            return lib.spice_file is not None

        def paths_func(lib: Library) -> List[str]:
            assert lib.spice_file is not None
            return [lib.spice_file]

        return LibraryFilter(
            tag="spice",
            description="SPICE files",
            is_file=True,
            filter_func=filter_func,
            paths_func=paths_func
        )

    @property
    def milkyway_lib_dir_filter(self) -> LibraryFilter:
        def select_milkyway_lib(lib: Library) -> List[str]:
            if lib.milkyway_lib_in_dir is not None:
                return [os.path.dirname(lib.milkyway_lib_in_dir)]
            else:
                return []

        return LibraryFilter(
            tag="milkyway_dir",
            description="Milkyway lib",
            is_file=False,
            paths_func=select_milkyway_lib
        )

    @property
    def milkyway_techfile_filter(self) -> LibraryFilter:
        """Select milkyway techfiles."""

        def select_milkyway_tfs(lib: Library) -> List[str]:
            if lib.milkyway_techfile is not None:
                return [lib.milkyway_techfile]
            else:
                return []

        return LibraryFilter(
            tag="milkyway_tf",
            description="Milkyway techfile",
            is_file=True,
            paths_func=select_milkyway_tfs,
            extra_post_filter_funcs=[self.create_nonempty_check("Milkyway techfile")]
        )

    @property
    def tlu_max_cap_filter(self) -> LibraryFilter:
        """Select TLU+ max cap files."""

        def select_tlu_max_cap(lib: Library) -> List[str]:
            if lib.tluplus_files is not None and lib.tluplus_files.max_cap is not None:
                return [lib.tluplus_files.max_cap]
            else:
                return []

        return LibraryFilter(
            tag="tlu_max",
            description="TLU+ max cap db",
            is_file=True,
            paths_func=select_tlu_max_cap
        )

    @property
    def tlu_min_cap_filter(self) -> LibraryFilter:
        """Select TLU+ min cap files."""

        def select_tlu_min_cap(lib: Library) -> List[str]:
            if lib.tluplus_files is not None and lib.tluplus_files.min_cap is not None:
                return [lib.tluplus_files.min_cap]
            else:
                return []

        return LibraryFilter(
            tag="tlu_min",
            description="TLU+ min cap db",
            is_file=True,
            paths_func=select_tlu_min_cap
        )

    @property
    def tlu_map_file_filter(self) -> LibraryFilter:
        """Select TLU+ map files."""
        def select_tlu_map_file(lib: Library) -> List[str]:
            if lib.tluplus_map_file is not None:
                return [lib.tluplus_map_file]
            else:
                return []
        return LibraryFilter(
            tag="tlu_map",
            description="TLU+ map file",
            is_file=True,
            paths_func=select_tlu_map_file
        )

    @property
    def spice_model_file_filter(self) -> LibraryFilter:
        """Select spice model files."""
        def select_spice_model_file(lib: Library) -> List[str]:
            if lib.spice_model_file is not None and lib.spice_model_file.path is not None:
                return [lib.spice_model_file.path]
            else:
                return []
        return LibraryFilter(
            tag="spice_model_file",
            description="Spice model file",
            is_file=True,
            paths_func=select_spice_model_file
        )

    @property
    def spice_model_lib_corner_filter(self) -> LibraryFilter:
        """Select spice model lib corners."""
        def select_spice_model_lib_corner(lib: Library) -> List[str]:
            if lib.spice_model_file is not None and lib.spice_model_file.lib_corner is not None:
                return [lib.spice_model_file.lib_corner]
            else:
                return []
        return LibraryFilter(
            tag="spice_model_lib_corner",
            description="Spice model lib corner",
            is_file=False,
            paths_func=select_spice_model_lib_corner
        )

    @property
    def power_grid_library_filter(self) -> LibraryFilter:
        """
        Select power grid libraries for EM/IR analysis.
        """

        def filter_func(lib: Library) -> bool:
            return lib.power_grid_library is not None

        def paths_func(lib: Library) -> List[str]:
            assert lib.power_grid_library is not None
            return [lib.power_grid_library]

        def sort_func(lib: Library):
            if lib.provides is not None:
                for provided in lib.provides:
                    if provided.lib_type is not None and provided.lib_type == "technology":
                        return 0  # put the technology library in front
            return 100  # put it behind

        return LibraryFilter(
            tag="power_grid_library",
            description="Power grid library",
            is_file=False,
            filter_func=filter_func,
            paths_func=paths_func,
            sort_func=sort_func
        )

    @property
    def klayout_techfile_filter(self) -> LibraryFilter:
        """
        Select KLayout tech files for GDS streaming.
        """

        def filter_func(lib: Library) -> bool:
            return lib.klayout_techfile is not None

        def paths_func(lib: Library) -> List[str]:
            assert lib.klayout_techfile is not None
            return [lib.klayout_techfile]

        return LibraryFilter(
            tag="klayout",
            description="GDS streaming",
            is_file=True,
            filter_func=filter_func,
            paths_func=paths_func
        )


# Holds the list of pre-implemented filters.
# Access it like hammer_tech.filters.lef_filter
filters = LibraryFilterHolder()
