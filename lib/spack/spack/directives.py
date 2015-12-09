##############################################################################
# Copyright (c) 2013, Lawrence Livermore National Security, LLC.
# Produced at the Lawrence Livermore National Laboratory.
#
# This file is part of Spack.
# Written by Todd Gamblin, tgamblin@llnl.gov, All rights reserved.
# LLNL-CODE-647188
#
# For details, see https://github.com/llnl/spack
# Please also see the LICENSE file for our notice and the LGPL.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License (as published by
# the Free Software Foundation) version 2.1 dated February 1999.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the IMPLIED WARRANTY OF
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the terms and
# conditions of the GNU General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
##############################################################################
"""This package contains directives that can be used within a package.

Directives are functions that can be called inside a package
definition to modify the package, for example:

    class OpenMpi(Package):
        depends_on("hwloc")
        provides("mpi")
        ...

``provides`` and ``depends_on`` are spack directives.

The available directives are:

  * ``version``
  * ``depends_on``
  * ``provides``
  * ``extends``
  * ``patch``
  * ``variant``

"""
__all__ = [ 'depends_on', 'extends', 'provides', 'patch', 'version',
            'variant' ]

import re
import inspect

from llnl.util.lang import *

import spack
import spack.spec
import spack.error
import spack.url
from spack.version import Version
from spack.patch import Patch
from spack.variant import Variant
from spack.spec import Spec, parse_anonymous_spec


#
# This is a list of all directives, built up as they are defined in
# this file.
#
directives = {}


def ensure_dicts(pkg):
    """Ensure that a package has all the dicts required by directives."""
    for name, d in directives.items():
        d.ensure_dicts(pkg)


class directive(object):
    """Decorator for Spack directives.

    Spack directives allow you to modify a package while it is being
    defined, e.g. to add version or depenency information.  Directives
    are one of the key pieces of Spack's package "langauge", which is
    embedded in python.

    Here's an example directive:

        @directive(dicts='versions')
        version(pkg, ...):
            ...

    This directive allows you write:

        class Foo(Package):
            version(...)

    The ``@directive`` decorator handles a couple things for you:

      1. Adds the class scope (pkg) as an initial parameter when
         called, like a class method would.  This allows you to modify
         a package from within a directive, while the package is still
         being defined.

      2. It automatically adds a dictionary called "versions" to the
         package so that you can refer to pkg.versions.

    The ``(dicts='versions')`` part ensures that ALL packages in Spack
    will have a ``versions`` attribute after they're constructed, and
    that if no directive actually modified it, it will just be an
    empty dict.

    This is just a modular way to add storage attributes to the
    Package class, and it's how Spack gets information from the
    packages to the core.

    """

    def __init__(self, dicts=None):
        if isinstance(dicts, basestring):
            dicts = (dicts,)
        elif type(dicts) not in (list, tuple):
            raise TypeError(
                "dicts arg must be list, tuple, or string. Found %s."
                % type(dicts))

        self.dicts = dicts


    def ensure_dicts(self, pkg):
        """Ensure that a package has the dicts required by this directive."""
        for d in self.dicts:
            if not hasattr(pkg, d):
                setattr(pkg, d, {})

            attr = getattr(pkg, d)
            if not isinstance(attr, dict):
                raise spack.error.SpackError(
                    "Package %s has non-dict %s attribute!" % (pkg, d))


    def __call__(self, directive_function):
        directives[directive_function.__name__] = self

        def wrapped(*args, **kwargs):
            pkg = DictWrapper(caller_locals())
            self.ensure_dicts(pkg)

            pkg.name = get_calling_module_name()
            return directive_function(pkg, *args, **kwargs)

        return wrapped


@directive('versions')
def version(pkg, ver, checksum=None, **kwargs):
    """Adds a version and metadata describing how to fetch it.
       Metadata is just stored as a dict in the package's versions
       dictionary.  Package must turn it into a valid fetch strategy
       later.
    """
    # TODO: checksum vs md5 distinction is confusing -- fix this.
    # special case checksum for backward compatibility
    if checksum:
        kwargs['md5'] = checksum

    # Store kwargs for the package to later with a fetch_strategy.
    pkg.versions[Version(ver)] = kwargs


def _depends_on(pkg, spec, when=None):
    if when is None:
        when = pkg.name
    when_spec = parse_anonymous_spec(when, pkg.name)

    dep_spec = Spec(spec)
    if pkg.name == dep_spec.name:
        raise CircularReferenceError('depends_on', pkg.name)

    conditions = pkg.dependencies.setdefault(dep_spec.name, {})
    if when_spec in conditions:
        conditions[when_spec].constrain(dep_spec, deps=False)
    else:
        conditions[when_spec] = dep_spec


@directive('dependencies')
def depends_on(pkg, spec, when=None):
    """Creates a dict of deps with specs defining when they apply."""
    _depends_on(pkg, spec, when=when)


@directive(('extendees', 'dependencies'))
def extends(pkg, spec, **kwargs):
    """Same as depends_on, but dependency is symlinked into parent prefix.

    This is for Python and other language modules where the module
    needs to be installed into the prefix of the Python installation.
    Spack handles this by installing modules into their own prefix,
    but allowing ONE module version to be symlinked into a parent
    Python install at a time.

    keyword arguments can be passed to extends() so that extension
    packages can pass parameters to the extendee's extension
    mechanism.

    """
    if pkg.extendees:
        raise DirectiveError("Packages can extend at most one other package.")

    when = kwargs.pop('when', pkg.name)
    _depends_on(pkg, spec, when=when)
    pkg.extendees[spec] = (Spec(spec), kwargs)


@directive('provided')
def provides(pkg, *specs, **kwargs):
    """Allows packages to provide a virtual dependency.  If a package provides
       'mpi', other packages can declare that they depend on "mpi", and spack
       can use the providing package to satisfy the dependency.
    """
    spec_string = kwargs.get('when', pkg.name)
    provider_spec = parse_anonymous_spec(spec_string, pkg.name)

    for string in specs:
        for provided_spec in spack.spec.parse(string):
            if pkg.name == provided_spec.name:
                raise CircularReferenceError('depends_on', pkg.name)
            pkg.provided[provided_spec] = provider_spec


@directive('patches')
def patch(pkg, url_or_filename, level=1, when=None):
    """Packages can declare patches to apply to source.  You can
       optionally provide a when spec to indicate that a particular
       patch should only be applied when the package's spec meets
       certain conditions (e.g. a particular version).
    """
    if when is None:
        when = pkg.name
    when_spec = parse_anonymous_spec(when, pkg.name)

    cur_patches = pkg.patches.setdefault(when_spec, [])
    # if this spec is identical to some other, then append this
    # patch to the existing list.
    cur_patches.append(Patch(pkg.name, url_or_filename, level))


@directive('variants')
def variant(pkg, name, default=False, description=""):
    """Define a variant for the package. Packager can specify a default
    value (on or off) as well as a text description."""

    default     = bool(default)
    description = str(description).strip()

    if not re.match(spack.spec.identifier_re, name):
        raise DirectiveError("Invalid variant name in %s: '%s'" % (pkg.name, name))

    pkg.variants[name] = Variant(default, description)


class DirectiveError(spack.error.SpackError):
    """This is raised when something is wrong with a package directive."""
    def __init__(self, directive, message):
        super(DirectiveError, self).__init__(message)
        self.directive = directive


class CircularReferenceError(DirectiveError):
    """This is raised when something depends on itself."""
    def __init__(self, directive, package):
        super(CircularReferenceError, self).__init__(
            directive,
            "Package '%s' cannot pass itself to %s." % (package, directive))
        self.package = package
