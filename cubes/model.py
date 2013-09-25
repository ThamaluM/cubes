# -*- coding=utf -*-
"""Logical model."""

import os
import re
import urllib2
import urlparse
import copy
try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict

from .common import IgnoringDictionary, get_logger, to_label
from .errors import *
from .extensions import get_namespace, initialize_namespace

try:
    import json
except ImportError:
    import simplejson as json
__all__ = [
    "read_model_metadata",
    "read_model_metadata_bundle",
    "create_model_provider",
    "ModelProvider",

    "create_dimension",

    "attribute_list",
    "coalesce_attribute",

    "Model",
    "Cube",
    "Dimension",
    "Hierarchy",
    "Level",
    "Attribute",
    "simple_model",
    "split_aggregate_ref",
    "aggregate_ref",

    # FIXME: Depreciated
    "load_model",
    "model_from_path",
    "create_model",
    "merge_models",
]

# Model object:
#
# name
# description
# info
# 
# dimensions
# cubes
# public_dimensions (list of names)
#
# joins
# mappings
# browser_options
# store
# provider
# 

RECORD_COUNT_MEASURE = { 'name': 'record', 'label': 'Count', 'aggregations': [ 'count', 'sma' ] }


def create_model_provider(name, metadata, store, store_name):
    """Gets a new instance of a model provider with name `name`."""

    ns = get_namespace("model_providers")
    if not ns:
        ns = initialize_namespace("model_providers", root_class=ModelProvider,
                                  suffix="_model_provider")

    try:
        factory = ns[name]
    except KeyError:
        raise CubesError("Unable to find model provider of type '%s'" % name)

    return factory(metadata, store, store_name)

def _json_from_url(url):
    """Opens `resource` either as a file with `open()`or as URL with
    `urllib2.urlopen()`. Returns opened handle. """

    parts = urlparse.urlparse(url)
    if parts.scheme in ('', 'file'):
        handle = open(parts.path)
    else:
        handle = urllib2.urlopen(url)

    try:
        desc = json.load(handle)
    except ValueError as e:
        raise SyntaxError("Syntax error in %s: %s" % (url, e.args))
    finally:
        handle.close()

    return desc


def read_model_metadata(source):
    """Reads a model description from `source` which can be a filename, URL,
    file-like object or a path to a directory. Returns a model description
    dictionary."""

    if isinstance(source, basestring):
        parts = urlparse.urlparse(source)
        if parts.scheme in ('', 'file') and os.path.isdir(parts.path):
            source = parts.path
            return read_model_metadata_bundle(source)
        else:
            return _json_from_url(source)
    else:
        return json.load(source)


def read_model_metadata_bundle(path):
    """Load logical model a directory specified by `path`.  Returns a model
    description dictionary."""

    if not os.path.isdir(path):
        raise ArgumentError("Path '%s' is not a directory.")

    info_path = os.path.join(path, 'model.json')

    if not os.path.exists(info_path):
        raise ModelError('main model info %s does not exist' % info_path)

    model = _json_from_url(info_path)

    # Find model object files and load them

    if not "dimensions" in model:
        model["dimensions"] = []

    if not "cubes" in model:
        model["cubes"] = []

    for dirname, dirnames, filenames in os.walk(path):
        for filename in filenames:
            if os.path.splitext(filename)[1] != '.json':
                continue

            split = re.split('_', filename)
            prefix = split[0]
            obj_path = os.path.join(dirname, filename)

            if prefix in ('dim', 'dimension'):
                desc = _json_from_url(obj_path)
                try:
                    name = desc["name"]
                except KeyError:
                    raise ModelError("Dimension file '%s' has no name key" %
                                                                     obj_path)
                if name in model["dimensions"]:
                    raise ModelError("Dimension '%s' defined multiple times " %
                                        "(in '%s')" % (name, obj_path) )
                model["dimensions"].append(desc)

            elif prefix == 'cube':
                desc = _json_from_url(obj_path)
                try:
                    name = desc["name"]
                except KeyError:
                    raise ModelError("Cube file '%s' has no name key" %
                                                                     obj_path)
                if name in model["cubes"]:
                    raise ModelError("Cube '%s' defined multiple times "
                                        "(in '%s')" % (name, obj_path) )
                model["cubes"].append(desc)

    return model


def load_model(resource, translations=None):
    raise Exception("load_model() was replaced by Workspace.add_model(), "
                    "please refer to the documentation for more information")



def fix_dimension_metadata(metadata):
    """Returns a dimension description as a dictionary. If provided as string,
    then it is going to be used as a name and as a single level."""

    if isinstance(metadata, basestring):
        return {"name":metadata, "levels": [metadata]}
    else:
        return metadata


def fix_level_metadata(metadata):
    """Returns a level description as a dictionary. If provided as string,
    then it is going to be used as level name and as its only attribute. If a
    dictionary is provided and has no attributes, then level will contain only
    attribute with the same name as the level name."""
    if isinstance(metadata, basestring):
        return {"name":metadata, "attributes": [metadata]}
    else:
        if "attributes" not in metadata:
            metadata = dict(metadata)
            try:
                metadata["attributes"] = [metadata["name"]]
            except KeyError:
                raise ModelError("Level has no name.")

        return metadata

class ModelProvider(object):
    """Abstract class. Currently empty and used only to find other model
    providers."""

    def __init__(self, metadata=None, store=None, store_name=None):
        """Initializes a model provider and sets `metadata` – a model metadata
        dictionary.

        Instance variable `store` might be populated after the
        initialization. If the model provider requires an open store, it
        should advertise it through `True` value returned by provider's
        `requires_store()` method.  Otherwise no store is opened for the model
        provider. `store_name` is also set.

        Subclasses should call this method when they are implementing custom
        `__init__()`.

        """
        self.metadata = metadata
        self.store = store
        self.store_name = store_name

        # TODO: check for duplicates
        self.dimensions_metadata = {}
        for dim in metadata.get("dimensions", []):
            self.dimensions_metadata[dim["name"]] = dim

        self.cubes_metadata = {}
        for cube in metadata.get("cubes", []):
            self.cubes_metadata[cube["name"]] = cube

        self.options = metadata.get("options", {})

    def cube_options(self, cube_name):
        """Returns an options dictionary for cube `name`. The options
        dictoinary is merged model `options` metadata with cube's `options`
        metadata if exists. Cube overrides model's global (default)
        options."""

        options = dict(self.options)
        if cube_name in self.cubes_metadata:
            cube = self.cubes_metadata[cube_name]
            options.update(cube.get("options", {}))

        return options

    def list_cubes(self):
        """Get a list of metadata for cubes in the workspace. Result is a list
        of dictionaries with keys: `name`, `label`, `category`, `info`.

        The list is fetched from the model providers on the call of this
        method.
        """
        raise NotImplementedError("Subclasses should implement list_cubes()")
        return []

    def cube(self, name):
        """Returns a cube with `name` provided by the receiver. If receiver
        does not have the cube `ModelError` exception is raised.

        Returned cube has no dimensions assigned. You should assign the
        dimensions according to the cubes `linked_dimensions` list of
        dimension names."""
        raise NotImplementedError("Subclasses should implement cube() method")

    def dimension(self, name, dimensions=[]):
        """Returns a dimension with `name` provided by the receiver.
        `dimensions` is a dictionary of dimension objects where the receiver
        can look for templates. If the dimension requires a template and the
        template is missing, the subclasses should raise
        `TemplateRequired(template)` error with a template name as an
        argument.

        If the receiver does not provide the dimension `NoSuchDimension`
        exception is raised."""
        raise NotImplementedError("Subclasses are required to implement this")


class DefaultModelProvider(ModelProvider):

    dynamic_cubes = False
    dynamic_dimensions = False

    def __init__(self, metadata, store, store_name):
        super(DefaultModelProvider, self).__init__(metadata, store, store_name)


    def list_cubes(self):
        cubes = []

        for cube in self.metadata.get("cubes", []):
            info = {
                    "name": cube["name"],
                    "label": cube.get("label", cube["name"]),
                    "category": (cube.get("category") or cube.get("info", {}).get("category")),
                    "info": cube.get("info", {})
                }
            cubes.append(info)

        return cubes

    def cube(self, name):
        """
        Creates a cube `name` in context of `workspace` from provider's
        metadata. Cube has no dimensions. You should link the dimensions from
        list of `linked_dimensions`.
        """

        if name in self.cubes_metadata:
            metadata = dict(self.cubes_metadata[name])
        else:
            raise ModelError("Unknown cube --- %s" % name)

        # Merge model and cube mappings
        #
        model_mappings = self.metadata.get("mappings")
        cube_mappings = metadata.pop("mappings", None)

        if model_mappings:
            mappings = copy.deepcopy(model_mappings)
            mappings.update(cube_mappings)
        else:
            mappings = cube_mappings

        # Merge model and cube joins
        #
        model_joins = self.metadata.get("joins")
        cube_joins = metadata.pop("joins", None)

        # merge datastore from model if datastore not present
        if not metadata.get("datastore"):
            metadata['datastore'] = self.metadata.get("datastore")

        # merge browser_options
        browser_options = self.metadata.get('browser_options', {})
        if metadata.get('browser_options'):
            browser_options.update(metadata.get('browser_options'))
        metadata['browser_options'] = browser_options

        # model joins, if present, should be merged with cube's overrides.
        # joins are matched by the "name" key.
        if cube_joins and model_joins:
            model_join_map = {}
            for join in model_joins:
                try:
                    name = join['name']
                except KeyError:
                    raise ModelError("Missing required 'name' key in "
                                     "model-level joins.")

                if name in model_join_map:
                    raise ModelError("Duplicate model-level join 'name': %s" %
                                     name)

                model_join_map[name] = copy.deepcopy(join)

            # Merge cube's joins with model joins by their names.
            merged_joins = []

            for join in cube_joins:
                model_join = model_join_map.get(join.get('name'), {})
                model_join.update(join)
                merged_joins.append(model_join)

            cube_joins = merged_joins

        dimensions = metadata.pop("dimensions", [])

        return Cube(linked_dimensions=dimensions,
                    mappings=mappings,
                    joins=cube_joins,
                    **metadata)

    def dimension(self, name, dimensions=None):
        """Create a dimension `name` from provider's metadata within
        `context` (usualy a `Workspace` object)."""

        # Old documentation
        """Creates a `Dimension` instance from `obj` which can be a `Dimension`
        instance or a string or a dictionary. If it is a string, then it
        represents dimension name, the only level name and the only attribute.

        Keys of a dictionary representation:

        * `name`: dimension name
        * `levels`: list of dimension levels (see: :class:`cubes.Level`)
        * `hierarchies` or `hierarchy`: list of dimension hierarchies or
           list of level names of a single hierarchy. Only one of the two
           should be specified, otherwise an exception is raised.
        * `default_hierarchy_name`: name of a hierarchy that will be used when
          no hierarchy is explicitly specified
        * `label`: dimension name that will be displayed (human readable)
        * `description`: human readable dimension description
        * `info` - custom information dictionary, might be used to store
          application/front-end specific information (icon, color, ...)
        * `template` – name of a dimension to be used as template. The dimension
          is taken from `dimensions` argument which should be a dictionary
          of already created dimensions.

        **Defaults**

        * If no levels are specified during initialization, then dimension
          name is considered flat, with single attribute.
        * If no hierarchy is specified and levels are specified, then default
          hierarchy will be created from order of levels
        * If no levels are specified, then one level is created, with name
          `default` and dimension will be considered flat

        String representation of a dimension ``str(dimension)`` is equal to
        dimension name.

        Class is not meant to be mutable.

        Raises `ModelInconsistencyError` when both `hierarchy` and
        `hierarchies` is specified.

        """
        try:
            metadata = dict(self.dimensions_metadata[name])
        except KeyError:
            raise NoSuchDimensionError(name)

        return create_dimension(metadata, dimensions, name)

def create_dimension(metadata, dimensions=None, name=None):
    """Create a dimension from a `metadata` dictionary."""

    dimensions = dimensions or {}

    if "template" in metadata:
        template_name = metadata["template"]
        try:
            template = dimensions[template_name]
        except KeyError:
            raise TemplateRequired(template_name)

        levels = copy.deepcopy(template.levels)

        # Create copy of template's hierarchies, but reference newly
        # created copies of level objects
        hierarchies = []
        level_dict = dict((level.name, level) for level in levels)

        for hier in template.hierarchies.values():
            hier_levels = [level_dict[level.name] for level in hier.levels]
            hier_copy = Hierarchy(hier.name,
                                  hier_levels,
                                  label=hier.label,
                                  info=copy.deepcopy(hier.info))
            hierarchies.append(hier_copy)

        default_hierarchy_name = template.default_hierarchy_name
        label = template.label
        description = template.description
        info = template.info
    else:
        levels = None
        hierarchies = None
        default_hierarchy_name = None
        label = None
        description = None
        info = {}

    label = metadata.get("label") or label
    description = metadata.get("description") or description
    info = metadata.get("info") or info

    # Levels
    # ------

    levels_metadata = metadata.get("levels")

    if levels_metadata:
        levels = []

        for md in levels_metadata:
            level = Level(**fix_level_metadata(md))
            levels.append(level)

    if not levels:
        # Create a single level with same properties as the dimension.
        attributes = ["attributes", "key", "order_attribute", "order",
                      "label_attribute"]
        level_md = {}
        for attr in attributes:
            if attr in metadata:
                level_md[attr] = metadata[attr]

        # Default: if no attributes, then there is single flat attribute
        # whith same name as the dimension
        level_md["name"] = name
        level_md["key"] = name
        level_md["label"] = label
        level_md = fix_level_metadata(level_md)

        levels = [Level(**level_md)]

    # Hierarchies
    # -----------

    if "hierarchy" in metadata and "hierarchies" in metadata:
        raise ModelInconsistencyError("Both 'hierarchy' and 'hierarchies'"
                                      " specified. Use only one")

    hierarchy = metadata.get("hierarchy")

    if hierarchy:
        # We consider it to be a list of level names
        if not isinstance(hierarchy, Hierarchy):
            hierarchy = Hierarchy("default", levels=hierarchy)

        hierarchies = [hierarchy]

    elif "hierarchies" in metadata:
        hierarchies = [Hierarchy(**md) for md in metadata["hierarchies"]]

    default_hierarchy_name = metadata.get("default_hierarchy_name",
                                          default_hierarchy_name)

    name = name or metadata["name"]

    return Dimension(name=name,
                     levels=levels,
                     hierarchies=hierarchies,
                     default_hierarchy_name=default_hierarchy_name,
                     label=label,
                     description=description,
                     info=info
                     )

# TODO: is this still necessary?

def merge_models(models):
    """Merge multiple models into one."""

    dimensions = {}
    all_cubes = {}
    name = None
    label = None
    description = None
    info = {}
    locale = None

    for model in models:
        if name is None and model.name:
            name = model.name
        if label is None and model.label:
            label = model.label
        if description is None and model.description:
            description = model.description
        if info is None and model.info:
            info = copy.deepcopy(model.info)
        if locale is None and model.locale:
            locale = model.locale

        # dimensions, fail on conflicting names
        for dim in model.dimensions:
            if dimensions.has_key(dim.name):
                raise ModelError("Found duplicate dimension named '%s', cannot merge models" % dim.name)
            dimensions[dim.name] = dim

        # cubes, fail on conflicting names
        for cube in model.cubes.values():
            if all_cubes.has_key(cube.name):
                raise ModelError("Found duplicate cube named '%s', cannot merge models" % cube.name)
            model.remove_cube(cube)
            if cube.info is None:
                cube.info = {}
            cube.info.update(model.info if model.info else {})
            all_cubes[cube.name] = cube

    return Model(name=name,
                 label=label,
                 description=description,
                 info=info,
                 dimensions=dimensions.values(),
                 cubes=all_cubes.values())

def create_model(source):
    raise NotImplementedError("create_model() is depreciated, use Workspace.add_model()")


def model_from_path(path):
    """Load logical model from a file or a directory specified by `path`.
    Returs instance of `Model`. """
    raise NotImplementedError("model_from_path is depreciated. use Workspace.add_model()")

def _assert_instance(obj, class_, label):
    """Raises ModelInconsistencyError when `obj` is not instance of `cls`"""
    if not isinstance(obj, class_):
        raise ModelInconsistencyError("%s should be sublcass of %s, "
                                      "provided: %s" % (label,
                                                        class_.__name__,
                                                        type(obj).__name__))

# TODO: modernize
def simple_model(cube_name, dimensions, measures):
    """Create a simple model with only one cube with name `cube_name`and flat
    dimensions. `dimensions` is a list of dimension names as strings and
    `measures` is a list of measure names, also as strings. This is
    convenience method mostly for quick model creation for denormalized views
    or tables with data from a single CSV file.

    Example:

    .. code-block:: python

        model = simple_model("contracts",
                             dimensions=["year", "supplier", "subject"],
                             measures=["amount"])
        cube = model.cube("contracts")
        browser = workspace.create_browser(cube)
    """
    dim_instances = []
    for dim_name in dimensions:
        dim_instances.append(create_dimension(dim_name))

    cube = Cube(cube_name, dim_instances, measures)

    return Model(cubes=[cube])


class Model(object):
    def __init__(self, name=None, cubes=None, dimensions=None, locale=None,
                 label=None, description=None, info=None, mappings=None,
                 provider=None, metadata=None, translations=None):
        """
        Logical representation of data. Base container for cubes and
        dimensions.

        Attributes:

        * `name` - model name
        * `cubes` -  list of `Cube` instances
        * `dimensions` - list of `Dimension` instances
        * `locale` - locale code of the model
        * `label` - human readable name - can be used in an application
        * `description` - longer human-readable description of the model
        * `info` - custom information dictionary

        * `metadata` – a dictionary describing the model
        * `provider` – an object that creates model objects

        """
        # * `mappings` – model-wide mappings of logical-to-physical attributes

        # Basic information
        self.name = name
        self.label = label
        self.description = description
        self.locale = locale
        self.info = info or {}
        self.provider = provider
        self.metadata = metadata

        # Physical information
        self.mappings = mappings

        self._dimensions = OrderedDict()
        if dimensions:
            for dim in dimensions:
                self.add_dimension(dim)

        self.cubes = OrderedDict()
        if cubes:
            for cube in cubes:
                self.add_cube(cube)

        self.translations = translations or {}

    def __str__(self):
        return 'Model(%s)' % self.name

    def add_cube(self, cube):
        """Adds cube to the model and also assigns the model to the cube. If
        cube has a model assigned and it is not this model, then error is
        raised.

        Raises `ModelInconsistencyError` when trying to assing a cube that is
        already assigned to a different model or if trying to add a dimension
        with existing name but different specification.
        """

        _assert_instance(cube, Cube, "cube")

        # Collect dimensions from cube
        my_dimensions = set(self.dimensions)
        my_dimension_names = set([dim.name for dim in self.dimensions])

        for dimension in cube.dimensions:
            if dimension not in my_dimensions:
                if dimension.name not in my_dimension_names:
                    self.add_dimension(dimension)
                else:
                    raise ModelInconsistencyError("Dimension %s of cube %s has different specification as model's dimension"
                                            % (dimension.name, cube.name) )

        self.cubes[cube.name] = cube

    def remove_cube(self, cube):
        """Removes cube from the model"""
        del self.cubes[cube.name]

    def cube(self, cube):
        """Get a cube with name `name` or coalesce object to a cube."""
        try:
            if isinstance(cube, basestring):
                cube = self.cubes[cube]
        except KeyError as e:
            raise ModelError("No such cube '%s'" % str(e))
        return cube

    def add_dimension(self, dimension):
        """Add dimension to model. Replace dimension with same name"""
        _assert_instance(dimension, Dimension, "dimension")

        if dimension.name in self._dimensions:
            raise ModelInconsistencyError("Dimension '%s' already exists in model '%s'" % (dimension.name, self.name))

        self._dimensions[dimension.name] = dimension

    def remove_dimension(self, dimension):
        """Remove a dimension from receiver"""
        del self._dimensions[dimension.name]

    @property
    def dimensions(self):
        return self._dimensions.values()

    def dimension(self, dim):
        """Get dimension by name or by object. Raises `NoSuchDimensionError`
        when there is no dimension `dim`."""

        if isinstance(dim, basestring):
            if dim in self._dimensions:
                return self._dimensions[dim]
            else:
                raise NoSuchDimensionError("Unknown dimension with name '%s' "
                                           "in model '%s'" % (dim, self.name))
        elif dim.name in self._dimensions:
            return dim
        else:
            raise NoSuchDimensionError("Unknown dimension '%s' in "
                                       "model '%s'" % (dim, self.name))

    def to_dict(self, **options):
        """Return dictionary representation of the model. All object
        references within the dictionary are name based

        * `full_attribute_names` - if set to True then attribute names will be
          written as ``dimension_name.attribute_name``
        """

        out = IgnoringDictionary()

        out.setnoempty("name", self.name)
        out.setnoempty("label", self.label)
        out.setnoempty("description", self.description)
        out.setnoempty("info", self.info)

        dims = [dim.to_dict(**options) for dim in self._dimensions.values()]
        out.setnoempty("dimensions", dims)

        cubes = [cube.to_dict(**options) for cube in self.cubes.values()]
        out.setnoempty("cubes", cubes)

        if options.get("with_mappings"):
            out.setnoempty("mappings", self.mappings)

        return out

    def __eq__(self, other):
        if other is None or type(other) != type(self):
            return False
        if self.name != other.name or self.label != other.label \
            or self.description != other.description:
            return False
        elif self.dimensions != other.dimensions:
            return False
        elif self.cubes != other.cubes:
            return False
        elif self.info != other.info:
            return False
        return True

    def validate(self):
        """Validate the model, check for model consistency. Validation result
        is array of tuples in form: (validation_result, message) where
        validation_result can be 'warning' or 'error'.

        Returs: array of tuples
        """

        results = []

        ################################################################
        # 1. Chceck dimensions
        is_fatal = False
        for dim_name, dim in self._dimensions.items():
            if not issubclass(dim.__class__, Dimension):
                results.append(('error', "Dimension '%s' is not a subclass of Dimension class" % dim_name))
                is_fatal = True

        # We are not going to continue if there are no valid dimension objects, as more errors migh emerge
        if is_fatal:
            return results

        for dim in self.dimensions:
            results.extend(dim.validate())

        ################################################################
        # 2. Chceck cubes

        if not self.cubes:
            results.append( ('warning', 'No cubes defined') )
        else:
            for cube_name, cube in self.cubes.items():
                results.extend(cube.validate())

        return results

    def is_valid(self, strict=False):
        """Check whether model is valid. Model is considered valid if there
        are no validation errors. If you want to be sure that there are no
        warnings as well, set *strict* to ``True``. If `strict` is ``False``
        only errors are considered fatal, if ``True`` also warnings will make
        model invalid.

        Returns ``True`` when model is valid, otherwise returns ``False``.
        """
        results = self.validate()
        if not results:
            return True

        if strict:
            return False

        for result in results:
            if result[0] == 'error':
                return False

        return True

    def _add_translation(self, lang, translation):
        self.translations[lang] = translation

    def localize(self, translation):
        """Return localized version of the model.

        `translation` might be a string or a dicitonary. If it is a string,
        then it represents locale name from model's localizations provided on
        model creation. If it is a dictionary, it should contains full model
        translation that is going to be applied.


        Translation dictionary structure example::

            {
                "locale": "sk",
                "cubes": {
                    "sales": {
                        "label": "Predaje",
                        "measures":
                            {
                                "amount": "suma",
                                "discount": {"label": "zľava",
                                             "description": "uplatnená zľava"}
                            }
                    }
                },
                "dimensions": {
                    "date": {
                        "label": "Dátum"
                        "attributes": {
                            "year": "rok",
                            "month": {"label": "mesiac"}
                        },
                        "levels": {
                            "month": {"label": "mesiac"}
                        }
                    }
                }
            }

        .. note::

            Whenever master model changes, you should call this method to get
            actualized localization of the original model.
        """

        model = copy.deepcopy(self)

        if type(translation) == str or type(translation) == unicode:
            try:
                translation = self.translations[translation]
            except KeyError:
                raise ModelError("Model has no translation for %s" %
                                    translation)

        if "locale" not in translation:
            raise ValueError("No locale specified in model translation")

        model.locale = translation["locale"]
        localize_common(model, translation)

        if "cubes" in translation:
            for name, cube_trans in translation["cubes"].items():
                cube = model.cube(name)
                cube.localize(cube_trans)

        if "dimensions" in translation:
            dimensions = translation["dimensions"]
            for name, dim_trans in dimensions.items():
                # Use translation template if exists, similar to dimension
                # template
                template_name = dim_trans.get("template")

                if False and template_name:
                    try:
                        template = dimensions[template_name]
                    except KeyError:
                        raise ModelError("No translation template '%s' for "
                                "dimension '%s'" % (template_name, name) )

                    template = dict(template)
                    template.update(dim_trans)
                    dim_trans = template

                dim = model.dimension(name)
                dim.localize(dim_trans)

        return model

    def localizable_dictionary(self):
        """Get model locale dictionary - localizable parts of the model"""
        locale = {}
        locale.update(get_localizable_attributes(self))
        clocales = {}
        locale["cubes"] = clocales
        for cube in self.cubes.values():
            clocales[cube.name] = cube.localizable_dictionary()

        dlocales = {}
        locale["dimensions"] = dlocales
        for dim in self.dimensions:
            dlocales[dim.name] = dim.localizable_dictionary()

        return locale


class Cube(object):
    def __init__(self, name, dimensions=None, measures=None,
                 label=None, details=None, mappings=None, joins=None,
                 fact=None, key=None, description=None, browser_options=None,
                 info=None, linked_dimensions=None,
                 locale=None, category=None, datastore=None, **options):
        """Create a new Cube model object.

        Attributes:

        * `name`: cube name
        * `measures`: list of measure attributes
        * `dimensions`: list of dimensions (should be `Dimension` instances)
        * `label`: human readable cube label
        * `details`: list of detail attributes
        * `description` - human readable description of the cube
        * `key`: fact key field (if not specified, then backend default key
          will be used, mostly ``id`` for SLQ or ``_id`` for document based
          databases)
        * `info` - custom information dictionary, might be used to store
          application/front-end specific information
        * `locale`: cube's locale
        * `linked_dimensions` – dimensions to be linked to the cube

        Attributes used by backends:

        * `mappings` - backend-specific logical to physical mapping
          dictionary
        * `joins` - backend-specific join specification (used in SQL
          backend)
        * `fact` - fact dataset (table) name (physical reference)
        * `datastore` - name of datastore to use
        * `options` - dictionary of other options used by the backend - refer
          to the backend documentation to see what options are used (for
          example SQL browser might look here for ``denormalized_view`` in
          case of denormalized browsing)
        """

        self.name = name
        self.locale = locale

        # User-oriented metadata
        self.label = label
        self.description = description
        self.info = info or {}
        # backward compatibility
        self.category = category or self.info.get("category")

        # TODO: put this into the model provider
        if not measures:
            measures = [ copy.deepcopy(RECORD_COUNT_MEASURE) ]
        self.measures = attribute_list(measures)
        self.details = attribute_list(details)

        # Physical properties
        self.mappings = mappings
        self.fact = fact
        self.joins = joins
        self.key = key
        self.browser_options = browser_options or {}
        self.datastore = datastore or options.get("datastore")
        self.browser = options.get("browser")

        self.linked_dimensions = linked_dimensions or []
        self._dimensions = OrderedDict()

        if dimensions:
            if all([isinstance(dim, Dimension) for dim in dimensions]):
                for dim in dimensions:
                    self.add_dimension(dim)
            else:
                raise ModelError("Dimensions for cube initialization should be "
                                 "a list of Dimension instances.")

    def add_dimension(self, dimension):
        """Add dimension to cube. Replace dimension with same name. Raises
        `ModelInconsistencyError` when dimension with same name already exists
        in the receiver. """

        if not isinstance(dimension, Dimension):
            raise ArgumentError("Dimension added to cube '%s' is not a "
                                "Dimension instance." % self.name)

        if dimension.name in self._dimensions:
            raise ModelError("Dimension with name %s already exits "
                             "in cube %s" % (dimension.name, self.name))


        self._dimensions[dimension.name] = dimension

    def remove_dimension(self, dimension):
        """Remove a dimension from receiver. `dimension` can be either
        dimension name or dimension object."""

        dim = self.dimension(dimension)
        del self._dimensions[dim.name]

    @property
    def dimensions(self):
        return self._dimensions.values()

    def dimension(self, obj):
        """Get dimension object. If `obj` is a string, then dimension with
        given name is returned, otherwise dimension object is returned if it
        belongs to the cube.

        Raises `NoSuchDimensionError` when there is no such dimension.
        """

        # FIXME: raise better exception if dimension does not exist, but is in
        # the list of required dimensions

        if not obj:
            raise NoSuchDimensionError("Requested dimension should not be none (cube '%s')" % \
                                self.name)

        if isinstance(obj, basestring):
            if obj in self._dimensions:
                return self._dimensions[obj]
            else:
                raise NoSuchDimensionError("cube '%s' has no dimension '%s'" %
                                    (self.name, obj))
        elif isinstance(obj, Dimension):
             return obj
        else:
            raise NoSuchDimensionError("Invalid dimension or dimension reference '%s' for cube '%s'" %
                                    (obj, self.name))

    def measure(self, obj):
        """Get measure object. If `obj` is a string, then measure with given
        name is returned, otherwise measure object is returned if it belongs
        to the cube. Returned object is of `Attribute` type.

        Raises `NoSuchAttributeError` when there is no such measure or when
        there are multiple measures with the same name (which also means that
        the model is not valid).
        """

        if isinstance(obj, basestring):
            lookup = [m for m in self.measures if m.name == obj]
            if lookup:
                if len(lookup) == 1:
                    return lookup[0]
                else:
                    raise ModelInconsistencyError("multiple measures with the same name '%s' found" % obj)
            else:
                raise NoSuchAttributeError("cube '%s' has no measure '%s'" %
                                    (self.name, obj))
        elif isinstance(obj, Attribute):
             return obj
        else:
            raise NoSuchAttributeError("Invalid measure or measure reference '%s' for cube '%s'" %
                                    (obj, self.name))

    def get_measures(self, measures):
        """Get a list of measures as `Attribute` objects. If `measures` is
        `None` then all cube's measures are returned."""

        array = []

        for measure in measures or self.measures:
            array.append(self.measure(measure))

        return array

    def to_dict(self, expand_dimensions=False, with_mappings=True, **options):
        """Convert to a dictionary. If `with_mappings` is ``True`` (which is default) then `joins`,
        `mappings`, `fact` and `options` are included. Should be set to
        ``False`` when returning a dictionary that will be provided in an user
        interface or through server API.
        """

        out = IgnoringDictionary()
        out.setnoempty("name", self.name)
        out.setnoempty("info", self.info)
        out.setnoempty("category", self.category)

        if options.get("create_label"):
            out.setnoempty("label", self.label or to_label(self.name))
        else:
            out.setnoempty("label", self.label)

        measures = [m.to_dict(**options) for m in self.measures]
        out.setnoempty("measures", measures)

        details = [a.to_dict(**options) for a in self.details]
        out.setnoempty("details", details)

        if expand_dimensions:
            dims = [dim.to_dict() for dim in self.dimensions]
        else:
            dims = [dim.name for dim in self.dimensions]

        out.setnoempty("dimensions", dims)

        if with_mappings:
            out.setnoempty("mappings", self.mappings)
            out.setnoempty("fact", self.fact)
            out.setnoempty("joins", self.joins)
            out.setnoempty("browser_options", self.browser_options)

        out.setnoempty("key", self.key)

        return out

    def __eq__(self, other):
        if other is None or type(other) != type(self):
            return False
        if self.name != other.name or self.label != other.label \
            or self.description != other.description:
            return False
        elif self.dimensions != other.dimensions:
            return False
        elif self.measures != other.measures:
            return False
        elif self.details != other.details:
            return False
        elif self.mappings != other.mappings:
            return False
        elif self.joins != other.joins:
            return False
        elif self.options != other.options:
            return False
        elif self.info != other.info:
            return False
        return True

    def validate(self):
        """Validate cube. See Model.validate() for more information. """
        results = []

        # Check whether all attributes, measures and keys are Attribute objects
        # This is internal consistency chceck

        measures = set()

        for measure in self.measures:
            if not isinstance(measure, Attribute):
                results.append( ('error', "Measure '%s' in cube '%s' is not instance of Attribute" % (measure, self.name)) )
            if str(measure) in measures:
                results.append( ('error', "Duplicate measure '%s' in cube '%s'"\
                                            % (measure, self.name)) )
            else:
                measures.add(str(measure))

        details = set()
        for detail in self.details:
            if not isinstance(detail, Attribute):
                results.append( ('error', "Detail '%s' in cube '%s' is not instance of Attribute" % (detail, self.name)) )
            if str(detail) in details:
                results.append( ('error', "Duplicate detail '%s' in cube '%s'"\
                                            % (detail, self.name)) )
            elif str(detail) in measures:
                results.append( ('error', "Duplicate detail '%s' in cube '%s'"
                                          " - specified also as measure" \
                                            % (detail, self.name)) )
            else:
                details.add(str(detail))

        # 2. check whether dimension attributes are unique

        return results

    def localize(self, locale):
        localize_common(self,locale)

        attr_locales = locale.get("measures")
        if attr_locales:
            for attrib in self.measures:
                if attrib.name in attr_locales:
                    localize_common(attrib, attr_locales[attrib.name])

        attr_locales = locale.get("details")
        if attr_locales:
            for attrib in self.details:
                if attrib.name in attr_locales:
                    localize_common(attrib, attr_locales[attrib.name])

    def localizable_dictionary(self):
        locale = {}
        locale.update(get_localizable_attributes(self))

        mdict = {}
        locale["measures"] = mdict

        for measure in self.measures:
            mdict[measure.name] = measure.localizable_dictionary()

        mdict = {}
        locale["details"] = mdict

        for measure in self.details:
            mdict[measure.name] = measure.localizable_dictionary()

        return locale

    def __str__(self):
        return self.name

class Dimension(object):
    """
    Cube dimension.

    """
    def __init__(self, name, levels, hierarchies=None, default_hierarchy_name=None,
                 label=None, description=None, info=None, **desc):

        """Create a new dimension

        Attributes:

    	* `name`: dimension name
    	* `levels`: list of dimension levels (see: :class:`cubes.Level`)
    	* `hierarchies`: list of dimension hierarchies. If no hierarchies are
          specified, then default one is created from ordered list of `levels`.
        * `default_hierarchy_name`: name of a hierarchy that will be used when
          no hierarchy is explicitly specified
        * `label`: dimension name that will be displayed (human readable)
        * `description`: human readable dimension description
        * `info` - custom information dictionary, might be used to store
          application/front-end specific information (icon, color, ...)

        Dimension class is not meant to be mutable. All level attributes will
        have new dimension assigned.

        Note that the dimension will claim ownership of levels and their
        attributes. You should make sure that you pass a copy of levels if you
        are cloning another dimension.
        """

        self.name = name

        self.label = label
        self.description = description
        self.info = info or {}

        logger = get_logger()

        if not levels:
            raise ModelError("No levels specified for dimension %s" % self.name)

        self._set_levels(levels)

        if hierarchies:
            self.hierarchies = dict((hier.name, hier) for hier in hierarchies)
        else:
            hier = Hierarchy("default", self.levels)
            self.hierarchies = {"default": hier}

        # Claim ownership of hierarchies
        for hier in self.hierarchies.values():
            hier.dimension = self

        self._flat_hierarchy = None
        self.default_hierarchy_name = default_hierarchy_name

        # FIXME: is this needed anymore?
        self.key_field = desc.get("key_field")

    def _set_levels(self, levels):
        """Set dimension levels. `levels` should be a list of `Level` instances."""
        self._levels = OrderedDict()
        self._attributes = OrderedDict()

        try:
            for level in levels:
                self._levels[level.name] = level
        except AttributeError:
            raise ModelInconsistencyError("Levels in dimension %s do not look "
                                          "like Level instances" % self.name)

        # Collect attributes
        self._attributes = OrderedDict()
        for level in self.levels:
            self._attributes.update([(a.name, a) for a in level.attributes])

        for attr in self._attributes.values():
            attr.dimension = self

    def __eq__(self, other):
        if other is None or type(other) != type(self):
            return False
        if self.name != other.name or self.label != other.label \
            or self.description != other.description:
            return False
        elif self._default_hierarchy() != other._default_hierarchy():
            return False

        if self._levels != other._levels:
            return False

        if other.hierarchies != self.hierarchies:
            return False

        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    @property
    def has_details(self):
        """Returns ``True`` when each level has only one attribute, usually
        key."""

        return any([level.has_details for level in self._levels.values()])

    @property
    def levels(self):
        """Get list of all dimension levels. Order is not guaranteed, use a
        hierarchy to have known order."""
        return self._levels.values()

    @property
    def level_names(self):
        """Get list of level names. Order is not guaranteed, use a hierarchy
        to have known order."""
        return self._levels.keys()

    def level(self, obj):
        """Get level by name or as Level object. This method is used for
        coalescing value"""
        if isinstance(obj, basestring):
            if obj not in self._levels:
                raise KeyError("No level %s in dimension %s" % (obj, self.name))
            return self._levels[obj]
        elif isinstance(obj, Level):
            return obj
        else:
            raise ValueError("Unknown level object %s (should be a string or Level)" % obj)

    def hierarchy(self, obj=None):
        """Get hierarchy object either by name or as `Hierarchy`. If `obj` is
        ``None`` then default hierarchy is returned."""

        if obj is None:
            return self._default_hierarchy()
        if isinstance(obj, basestring):
            if obj not in self.hierarchies:
                raise ModelError("No hierarchy %s in dimension %s" % (obj, self.name))
            return self.hierarchies[obj]
        elif isinstance(obj, Hierarchy):
            return obj
        else:
            raise ValueError("Unknown hierarchy object %s (should be a string or Hierarchy instance)" % obj)

    def attribute(self, reference):
        """Get dimension attribute from `reference`."""
        return self._attributes[str(reference)]

    @property
    def default_hierarchy(self):
        """Get default hierarchy specified by ``default_hierarchy_name``, if
        the variable is not set then get a hierarchy with name *default*

        .. warning::

            Depreciated. Use `Dimension.hierarchy()` instead.

        """
        logger = get_logger()
        logger.warn("Dimension.default_hierarchy is depreciated, use "
                    "hierarchy() instead")
        return self._default_hierarchy()

    def _default_hierarchy(self):
        """Get default hierarchy specified by ``default_hierarchy_name``, if
        the variable is not set then get a hierarchy with name *default*"""

        if self.default_hierarchy_name:
            hierarchy_name = self.default_hierarchy_name
        else:
            hierarchy_name = "default"

        hierarchy = self.hierarchies.get(hierarchy_name)

        if not hierarchy:
            if len(self.hierarchies) == 1:
                hierarchy = self.hierarchies.values()[0]
            else:
                if not self.hierarchies:
                    if len(self.levels) == 1:
                        if not self._flat_hierarchy:
                            self._flat_hierarchy = Hierarchy(name=level.name,
                                                             dimension=self,
                                                             levels=[levels[0]])

                        return self._flat_hierarchy
                    elif len(self.levels) > 1:
                        raise ModelError("There are no hierarchies in dimenson %s "
                                       "and there are more than one level" % self.name)
                    else:
                        raise ModelError("There are no hierarchies in dimenson %s "
                                       "and there are no levels to make hierarchy from" % self.name)
                else:
                    raise ModelError("No default hierarchy specified in dimension '%s' " \
                                   "and there is more (%d) than one hierarchy defined" \
                                   % (self.name, len(self.hierarchies)))

        return hierarchy

    @property
    def is_flat(self):
        """Is true if dimension has only one level"""
        return len(self.levels) == 1

    def key_attributes(self):
        """Return all dimension key attributes, regardless of hierarchy. Order
        is not guaranteed, use a hierarchy to have known order."""

        return [level.key for level in self._levels.values()]

    def all_attributes(self):
        """Return all dimension attributes regardless of hierarchy. Order is
        not guaranteed, use :meth:`cubes.Hierarchy.all_attributes` to get
        known order. Order of attributes within level is preserved."""

        return list(self._attributes.values())

    def to_dict(self, **options):
        """Return dictionary representation of the dimension"""

        out = IgnoringDictionary()
        out.setnoempty("name", self.name)
        out.setnoempty("info", self.info)
        out.setnoempty("default_hierarchy_name", self.default_hierarchy_name)

        if options.get("create_label"):
            out.setnoempty("label", self.label or to_label(self.name))
        else:
            out.setnoempty("label", self.label)

        out["levels"] = [level.to_dict(**options) for level in self.levels]
        out["hierarchies"] = [hier.to_dict(**options) for hier in self.hierarchies.values()]

        # Use only for reading, during initialization these keys are ignored, as they are derived
        # They are provided here for convenience.
        out["is_flat"] = self.is_flat
        out["has_details"] = self.has_details

        return out

    def validate(self):
        """Validate dimension. See Model.validate() for more information. """
        results = []

        if not self.levels:
            results.append( ('error', "No levels in dimension '%s'" \
                                        % (self.name)) )
            return results

        if not self.hierarchies:
            msg = "No hierarchies in dimension '%s'" % (self.name)
            if self.is_flat:
                level = self.levels[0]
                results.append( ('default', msg + ", flat level '%s' will be used" % (level.name)) )
            elif len(self.levels) > 1:
                results.append( ('error', msg + ", more than one levels exist (%d)" % len(self.levels)) )
            else:
                results.append( ('error', msg) )
        else: # if self.hierarchies
            if not self.default_hierarchy_name:
                if len(self.hierarchies) > 1 and not "default" in self.hierarchies:
                    results.append( ('error', "No defaut hierarchy specified, there is "\
                                              "more than one hierarchy in dimension '%s'" % self.name) )
                # else:
                #     def_name = self.hierarchy().name
                #     results.append( ('default', "No default hierarchy name specified in dimension '%s', using "
                #                                 "'%s'"% (self.name, def_name)) )

        if self.default_hierarchy_name and not self.hierarchies.get(self.default_hierarchy_name):
            results.append( ('error', "Default hierarchy '%s' does not exist in dimension '%s'" %
                            (self.default_hierarchy_name, self.name)) )


        attributes = set()
        first_occurence = {}

        for level_name, level in self._levels.items():
            if not level.attributes:
                results.append( ('error', "Level '%s' in dimension '%s' has no attributes" % (level.name, self.name)) )
                continue

            if not level.key:
                attr = level.attributes[0]
                results.append( ('default', "Level '%s' in dimension '%s' has no key attribute specified, "\
                                            "first attribute will be used: '%s'"
                                            % (level.name, self.name, attr)) )

            if level.attributes and level.key:
                if level.key.name not in [a.name for a in level.attributes]:
                    results.append( ('error',
                                     "Key '%s' in level '%s' in dimension "
                                     "'%s' is not in level's attribute list" \
                                     % (level.key, level.name, self.name)) )

            for attribute in level.attributes:
                attr_name = attribute.ref()
                if attr_name in attributes:
                    first = first_occurence[attr_name]
                    results.append( ('error',
                                     "Duplicate attribute '%s' in dimension "
                                     "'%s' level '%s' (also defined in level "
                                     "'%s')" % (attribute, self.name,
                                              level_name, first)) )
                else:
                    attributes.add(attr_name)
                    first_occurence[attr_name] = level_name

                if not isinstance(attribute, Attribute):
                    results.append( ('error',
                                     "Attribute '%s' in dimension '%s' is "
                                     "not instance of Attribute" \
                                     % (attribute, self.name)) )

                if attribute.dimension is not self:
                    results.append( ('error',
                                     "Dimension (%s) of attribute '%s' does "
                                     "not match with owning dimension %s" \
                                     % (attribute.dimension, attribute,
                                     self.name)) )

        return results

    def __str__(self):
        return self.name

    def __repr__(self):
        return "<dimension: {name: '%s', levels: %s}>" % \
                            (self.name, self._levels.keys())

    def localize(self, locale):
        localize_common(self, locale)

        attr_locales = locale.get("attributes", {})

        for attrib in self.all_attributes():
            if attrib.name in attr_locales:
                localize_common(attrib, attr_locales[attrib.name])

        level_locales = locale.get("levels") or {}
        for level in self.levels:
            level_locale = level_locales.get(level.name)
            if level_locale:
                level.localize(level_locale)

        hier_locales = locale.get("hierarcies")
        if hier_locales:
            for hier in self.hierarchies:
                hier_locale = hier_locales.get(hier.name)
                hier.localize(hier_locale)

    def localizable_dictionary(self):
        locale = {}
        locale.update(get_localizable_attributes(self))

        ldict = {}
        locale["levels"] = ldict

        for level in self.levels:
            ldict[level.name] = level.localizable_dictionary()

        hdict = {}
        locale["hierarchies"] = hdict

        for hier in self.hierarchies.values():
            hdict[hier.name] = hier.localizable_dictionary()

        return locale

class Hierarchy(object):
    """Dimension hierarchy - specifies order of dimension levels.

    Attributes:

    * `name`: hierarchy name
    * `dimension`: dimension the hierarchy belongs to
    * `label`: human readable name
    * `levels`: ordered list of levels or level names from `dimension`
    * `info` - custom information dictionary, might be used to store
      application/front-end specific information

    Some collection operations might be used, such as ``level in hierarchy``
    or ``hierarchy[index]``. String value ``str(hierarchy)`` gives the
    hierarchy name.

    """
    def __init__(self, name, levels, dimension=None, label=None, info=None):
        self.name = name
        self.label = label
        self.info = info or {}

        # if not dimension:
        #     raise ModelInconsistencyError("No dimension specified for "
        #                                   "hierarchy %s" % self.name)
        self._level_refs = levels
        self._levels = None

        if dimension:
            self.dimension = dimension
            self._set_levels(levels)

    def _set_levels(self, levels):
        if not levels:
            raise ModelInconsistencyError("Hierarchy level list should not be "
                                          "empty (in %s)" % self.name)

        self._levels = OrderedDict()
        for level in levels:
            level = self.dimension.level(level)
            self._levels[level.name] = level

    @property
    def levels(self):
        if not self._levels:
            self._set_levels(self._level_refs)

        return self._levels.values()

    @property
    def levels_dict(self):
        if not self._levels:
            self._set_levels(self._level_refs)

        return self._levels

    def __eq__(self, other):
        if not other or type(other) != type(self):
            return False
        elif self.name != other.name or self.label != other.label:
            return False
        elif self.levels != other.levels:
            return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def __str__(self):
        return self.name

    def __len__(self):
        return len(self.levels)

    def __getitem__(self, item):
        return self.levels[item]

    def __contains__(self, item):
        if item in self.levels:
            return True
        return item in [level.name for level in self.levels]

    def levels_for_path(self, path, drilldown=False):
        """Returns levels for given path. If path is longer than hierarchy
        levels, `cubes.ArgumentError` exception is raised"""

        depth = 0 if not path else len(path)
        return self.levels_for_depth(depth, drilldown)

    def levels_for_depth(self, depth, drilldown=False):
        """Returns levels for given `depth`. If `path` is longer than
        hierarchy levels, `cubes.ArgumentError` exception is raised"""

        depth = depth or 0
        extend = 1 if drilldown else 0

        if depth + extend > len(self.levels):
            raise HierarchyError("Depth %d is longer than hierarchy levels %s (drilldown: %s)" % (depth, self._levels.keys(), drilldown))

        return self.levels[0:depth+extend]

    def next_level(self, level):
        """Returns next level in hierarchy after `level`. If `level` is last
        level, returns ``None``. If `level` is ``None``, then the first level
        is returned."""

        if not level:
            return self.levels[0]

        index = self.levels_dict.keys().index(str(level))
        if index + 1 >= len(self.levels):
            return None
        else:
            return self.levels[index + 1]

    def previous_level(self, level):
        """Returns previous level in hierarchy after `level`. If `level` is
        first level or ``None``, returns ``None``"""

        if level is None:
            return None

        index = self.levels_dict.keys().index(str(level))
        if index == 0:
            return None
        else:
            return self.levels[index - 1]

    def level_index(self, level):
        """Get order index of level. Can be used for ordering and comparing
        levels within hierarchy."""
        try:
            return self.levels_dict.keys().index(str(level))
        except ValueError:
            raise HierarchyError("Level %s is not part of hierarchy %s"
                                    % (str(level), self.name))

    def is_last(self, level):
        """Returns `True` if `level` is last level of the hierarchy."""

        return level == self.levels[-1]

    def rollup(self, path, level=None):
        """Rolls-up the path to the `level`. If `level` is ``None`` then path is
        rolled-up only one level.

        If `level` is deeper than last level of `path` the `cubes.HierarchyError`
        exception is raised. If `level` is the same as `path` level, nothing
        happens."""

        if level:
            last = self.level_index(level) + 1
            if last > len(path):
                raise HierarchyError("Can not roll-up: level '%s' in dimension "
                                    "'%s' is deeper than deepest element "
                                    "of path %s", str(level), self.dimension.name, path)
        else:
            if len(path) > 0:
                last = len(path) - 1
            else:
                last = None

        if last is None:
            return []
        else:
            return path[0:last]

    def path_is_base(self, path):
        """Returns True if path is base path for the hierarchy. Base path is a
        path where there are no more levels to be added - no drill down
        possible."""

        return path != None and len(path) == len(self.levels)

    def key_attributes(self):
        """Return all dimension key attributes as a single list."""

        return [level.key for level in self.levels]

    def all_attributes(self):
        """Return all dimension attributes as a single list."""

        attributes = []
        for level in self.levels:
            attributes.extend(level.attributes)

        return attributes

    def to_dict(self, **options):
        """Convert to dictionary. Keys:

        * `name`: hierarchy name
        * `label`: human readable label (localizable)
        * `levels`: level names

        """

        out = IgnoringDictionary()
        out.setnoempty("name", self.name)
        out.setnoempty("levels", [str(l) for l in self.levels])
        out.setnoempty("info", self.info)

        if options.get("create_label"):
            out.setnoempty("label", self.label or to_label(self.name))
        else:
            out.setnoempty("label", self.label)

        return out

    def localize(self, locale):
        localize_common(self,locale)

    def localizable_dictionary(self):
        locale = {}
        locale.update(get_localizable_attributes(self))

        return locale

class Level(object):
    """Object representing a hierarchy level. Holds all level attributes.

    This object is immutable, except localization. You have to set up all
    attributes in the initialisation process.

    Attributes:

    * `name`: level name
    * `dimension`: dimnesion the level is associated with
    * `attributes`: list of all level attributes. Raises `ModelError` when
      `attribute` list is empty.
    * `key`: name of level key attribute (for example: ``customer_number`` for
      customer level, ``region_code`` for region level, ``month`` for month
      level).  key will be used as a grouping field for aggregations. Key
      should be unique within level. If not specified, then the first
      attribute is used as key.
    * `order`: ordering of the level. `asc` for ascending, `desc` for
      descending or might be unspecified.
    * `order_attribute`: name of attribute that is going to be used for
      sorting, default is first attribute (usually key)
    * `label_attribute`: name of attribute containing label to be displayed
      (for example: ``customer_name`` for customer level, ``region_name`` for
      region level, ``month_name`` for month level)
    * `label`: human readable label of the level
    * `info`: custom information dictionary, might be used to store
      application/front-end specific information
    """

    def __init__(self, name, attributes, dimension = None, key=None,
                 order_attribute=None, order=None, label_attribute=None, label=None,
                 info=None):

        self.name = name
        self.dimension = dimension
        self.label = label
        self.info = info or {}

        if not attributes:
            raise ModelError("Attribute list should not be empty")

        self.attributes = attribute_list(attributes, dimension)

        if key:
            self.key = self.attribute(key)
        elif len(self.attributes) >= 1:
            self.key = self.attributes[0]
        else:
            raise ModelInconsistencyError("Attribute list should not be empty")

        # Set second attribute to be label attribute if label attribute is not
        # set. If dimension is flat (only one attribute), then use the only
        # key attribute as label.

        if label_attribute:
            self.label_attribute = self.attribute(label_attribute)
        else:
            if len(self.attributes) > 1:
                self.label_attribute = coalesce_attribute(self.attributes[1], dimension)
            else:
                self.label_attribute = self.key

        # Set first attribute to be order attribute if order attribute is not
        # set

        if order_attribute:
            try:
                self.order_attribute = self.attribute(order_attribute)
            except NoSuchAttributeError:
                raise NoSuchAttributeError("Unknown order attribute %s in "\
                                            "dimension %s, level %s" %
                                                (order_attribute,
                                                    str(self.dimension),
                                                    self.name))
        else:
            self.order_attribute = coalesce_attribute(self.attributes[0], dimension)

        self.order = order

    def __eq__(self, other):
        if not other or type(other) != type(self):
            return False
        elif self.name != other.name or self.label != other.label or self.key != other.key:
            return False
        elif self.label_attribute != other.label_attribute:
            return False
        elif self.order_attribute != other.order_attribute:
             return False

        if self.attributes != other.attributes:
            return False

        # for attr in other.attributes:
        #     if attr not in self.attributes:
        #         return False

        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def __str__(self):
        return self.name

    def __repr__(self):
        return str(self.to_dict())

    def __deepcopy__(self, memo):
        order_attribute = str(self.order_attribute) if self.order_attribute \
                                                                else None
        return Level(self.name,
                        attributes=copy.deepcopy(self.attributes,memo),
                        key=self.key.name,
                        order_attribute=order_attribute,
                        order=self.order,
                        label_attribute=self.label_attribute.name,
                        info=copy.copy(self.info),
                        label=copy.copy(self.label)
                        )

    def to_dict(self, full_attribute_names=False, **options):
        """Convert to dictionary"""

        out = IgnoringDictionary()
        out.setnoempty("name", self.name)
        out.setnoempty("info", self.info)

        if options.get("create_label"):
            out.setnoempty("label", self.label or to_label(self.name))
        else:
            out.setnoempty("label", self.label)


        if full_attribute_names:
            out.setnoempty("key", self.key.ref())
            out.setnoempty("label_attribute", self.label_attribute.ref())
            out.setnoempty("order_attribute", self.order_attribute.ref())
        else:
            out.setnoempty("key", self.key.name)
            out.setnoempty("label_attribute", self.label_attribute.name)
            out.setnoempty("order_attribute", self.order_attribute.name)

        out.setnoempty("order", self.order)

        array = []
        for attr in self.attributes:
            array.append(attr.to_dict(**options))
        out.setnoempty("attributes", array)

        return out

    def attribute(self, name):
        """Get attribute by `name`"""

        attrs = [attr for attr in self.attributes if attr.name == name]

        if attrs:
            return attrs[0]
        else:
            raise NoSuchAttributeError(name)

    @property
    def has_details(self):
        """Is ``True`` when level has more than one attribute, for all levels
        with only one attribute it is ``False``."""

        return len(self.attributes) > 1

    def localize(self, locale):
        localize_common(self,locale)

        if isinstance(locale, basestring):
            return

        attr_locales = locale.get("attributes")
        if attr_locales:
            logger = get_logger()
            logger.warn("'attributes' in localization dictionary of levels "
                        "is depreciated. Use list of `attributes` in "
                        "localization of dimension")

            for attrib in self.attributes:
                if attrib.name in attr_locales:
                    localize_common(attrib, attr_locales[attrib.name])

    def localizable_dictionary(self):
        locale = {}
        locale.update(get_localizable_attributes(self))

        adict = {}
        locale["attributes"] = adict

        for attribute in self.attributes:
            adict[attribute.name] = attribute.localizable_dictionary()

        return locale


def attribute_list(attributes, dimension=None, attribute_class=None):
    """Create a list of attributes from a list of strings or dictionaries.
    see :func:`cubes.coalesce_attribute` for more information."""

    if not attributes:
        return []

    new_list = [coalesce_attribute(attr, dimension, attribute_class) for attr in attributes]

    return new_list

def coalesce_attribute(obj, dimension=None, attribute_class=None):
    """Makes sure that the `obj` is an ``Attribute`` instance. If `obj` is a
    string, then new instance is returned. If it is a dictionary, then the
    dictionary values are used for ``Attribute``instance initialization."""

    attribute_class = attribute_class or Attribute

    if isinstance(obj, basestring):
        return attribute_class(obj,dimension=dimension)
    elif isinstance(obj, dict):
        return attribute_class(dimension=dimension,**obj)
    else:
        return obj


class Attribute(object):

    ASC = 'asc'
    DESC = 'desc'

    def __init__(self, name, label=None, locales=None, order=None,
                description=None,dimension=None, aggregations=None,
                info=None, format=None, **kwargs):
        """Cube attribute - represents any fact field/column

        Attributes:

        * `name` - attribute name, used as identifier
        * `label` - attribute label displayed to a user
        * `locales` = list of locales that the attribute is localized to
        * `order` - default order of this attribute. If not specified, then
          order is unexpected. Possible values are: ``'asc'`` or ``'desc'``.
          It is recommended and safe to use ``Attribute.ASC`` and
          ``Attribute.DESC``
        * `aggregations` - list of default aggregations to be performed on
          this attribute if it is a measure. It is backend-specific, but most
          common might be: ``'sum'``, ``'min'``, ``'max'``, ...
        * `info` - custom information dictionary, might be used to store
          application/front-end specific information
        * `format` - application-specific display format information, useful
          for formatting numeric values of measure attributes

        String representation of the `Attribute` returns its `name` (without
        dimension prefix).

        `cubes.ArgumentError` is raised when unknown ordering type is
        specified.
        """
        super(Attribute, self).__init__()
        self.name = name
        self.label = label
        self.description = description
        self.dimension = dimension
        self.aggregations = aggregations
        self.info = info or {}
        self.format = format

        if order:
            self.order = order.lower()
            if self.order.startswith("asc"):
                self.order = Attribute.ASC
            elif self.order.startswith("desc"):
                self.order = Attribute.DESC
            else:
                raise ArgumentError("Unknown ordering '%s' for attributes '%s'" % \
                                    (order, self.ref()) )
        else:
            self.order = None

        if locales == None:
            self.locales = []
        else:
            self.locales = locales

    def __deepcopy__(self, memo):
        return Attribute(self.name,
                         self.label,
                         dimension=self.dimension,
                         locales=copy.deepcopy(self.locales, memo),
                         order=copy.deepcopy(self.order, memo),
                         description=self.description,
                         aggregations=copy.deepcopy(self.aggregations, memo),
                         info=copy.deepcopy(self.info, memo),
                         format=self.format)

    def __str__(self):
        return self.name

    def __repr__(self):
        return str(self.to_dict())

    def __eq__(self, other):
        if type(other) != Attribute:
            return False

        return self.name == other.name \
                and self.label == other.label \
                and self.locales == other.locales \
                and str(self.dimension) == str(other.dimension)

    def __ne__(self,other):
        return not self.__eq__(other)

    def to_dict(self, **options):
        # FIXME: Depreciated key "full_name" in favour of "ref"
        d = {
                "name": self.name,
                "full_name": self.ref(),
                "ref": self.ref()
            }

        if options.get("create_label"):
            d["label"] = self.label or to_label(self.name)
        else:
            d["label"] = self.label

        if self.locales:
            d["locales"] = self.locales
        if self.order is not None:
            d["order"] = self.order
        if self.description is not None:
            d["description"] = self.description
        if self.aggregations is not None:
            d["aggregations"] = self.aggregations
        if self.info is not None:
            d["info"] = self.info
        if self.format is not None:
            d["format"] = self.format

        return d

    def ref(self, simplify=True, locale=None):
        """Return full attribute reference. Append `locale` if it is one of
        attribute's locales, otherwise raise `cubes.ArgumentError`. If
        `simplify` is ``True``, then reference to an attribute of flat
        dimension without details will be just the dimension name.

        .. warning::

            This method might be renamed.

        """
        if locale:
            if not self.locales:
                raise ArgumentError("Attribute '%s' is not loalizable "
                                    "(localization %s requested)"
                                        % (self.name, locale))
            elif locale not in self.locales:
                raise ArgumentError("Attribute '%s' has no localization %s "
                                    "(has: %s)"
                                        % (self.name, locale, self.locales))
            else:
                locale_suffix = "." + locale
        else:
            locale_suffix = ""

        if self.dimension:
            if simplify and (self.dimension.is_flat and not self.dimension.has_details):
                reference = self.dimension.name
            else:
                reference = self.dimension.name + '.' + str(self.name)
        else:
            reference = str(self.name)

        return reference + locale_suffix

    def full_name(self, dimension=None, locale=None, simplify=True):
        """Return full name of an attribute as if it was part of `dimension`.
        Append `locale` if it is one of attribute's locales, otherwise
        raise `cubes.ArgumentError`.

        .. warning:

            Depreciated. Use `Attribute.ref()` instead.

        """
        # Old behaviour: If no locale is specified and attribute is localized, then first locale from
        # list of locales is used.

        # FIXME: Deprecate dimension, use dimension on initialisation and each
        # attribute should have one assigned.

        if locale:
            if locale in self.locales:
                raise ArgumentError("Attribute '%s' has no localization %s" % self.name)
            else:
                locale_suffix = "." + locale
        else:
            locale_suffix = ""

        dimension = self.dimension or dimension

        if simplify and (dimension.is_flat and not dimension.has_details):
            return str(dimension) + locale_suffix
        else:
            return str(dimension) + "." + self.name + locale_suffix

    def localizable_dictionary(self):
        locale = {}
        locale.update(get_localizable_attributes(self))

        return locale

def aggregate_ref(measure, aggregate):
    """Creates a reference string for measure aggregate. Current
    implementation joins the measure name and aggregate name with an
    underscore character `'_'`. Use this method in in a backend to create
    valid aggregate reference. See also `split_aggregate_ref()`"""

    return "%s_%s" % (measure, aggregate)

def split_aggregate_ref(measure):
    """Splits aggregate measure reference into measure name and aggregate
    name. Use this method in presenters to correctly get measure name and
    aggregate name from aggregate reference that was created by
    `aggregate_ref()` function.
    """

    measure = str(measure)

    r = measure.rfind("_")

    if r == -1 or r >= len(measure)-1:
        if r == -1:
            meaning = measure + "_sum"
        else:
            meaning = measure + "sum"

        raise ArgumentError("Invalid aggregate reference '%s'. "
                            "Did you mean '%s'?"% (measure, meaning))

    return (measure[:r], measure[r+1:])

def localize_common(obj, trans):
    """Localize common attributes: label and description. `trans` should be a
    dictionary or a string. If it is just a string, then only `label` will be
    localized."""
    if isinstance(trans, basestring):
        obj.label = trans
    else:
        if "label" in trans:
            obj.label = trans["label"]
        if "description" in trans:
            obj.description = trans["description"]


def localize_attributes(attribs, translations):
    """Localize list of attributes. `translations` should be a dictionary with
    keys as attribute names, values are dictionaries with localizable
    attribute metadata, such as ``label`` or ``description``."""
    for (name, atrans) in translations.items():
        attrib = attribs[name]
        localize_common(attrib, atrans)


def get_localizable_attributes(obj):
    """Returns a dictionary with localizable attributes of `obj`."""

    # FIXME: use some kind of class attribute to get list of localizable attributes

    locale = {}
    if hasattr(obj,"label"):
        locale["label"] = obj.label

    if hasattr(obj, "description"):
        locale["description"] = obj.description

    return locale
