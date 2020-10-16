# -*- coding: utf-8 -*-
"""Subclass of `Group` that serves as a base class for representing pseudo potential families."""
import os
import re
from typing import Union, List, Tuple, Mapping

from aiida.common import exceptions
from aiida.common.lang import classproperty, type_check
from aiida.orm import Group, QueryBuilder
from aiida.plugins import DataFactory

from aiida_pseudo.data.pseudo import PseudoPotentialData

__all__ = ('PseudoPotentialFamily',)

StructureData = DataFactory('structure')


class PseudoPotentialFamily(Group):
    """Group to represent a pseudo potential family.

    This is a base class that provides most of the functionality but does not actually define what type of pseudo
    potentials can be contained. Subclasses should define the `_pseudo_type` class attribute to the data type of the
    pseudo potentials that are accepted. This *has* to be a subclass of `PseudoPotentialData`.
    """

    _pseudo_type = PseudoPotentialData
    _pseudos = None

    def __repr__(self):
        """Represent the instance for debugging purposes."""
        return f'{self.__class__.__name__}<{self.pk or self.uuid}>'

    def __str__(self):
        """Represent the instance for human-readable purposes."""
        return f'{self.__class__.__name__}<{self.label}>'

    def __init__(self, *args, **kwargs):
        """Validate that the `_pseudo_type` class attribute is a subclass of `PseudoPotentialData`."""
        if not issubclass(self._pseudo_type, PseudoPotentialData):
            class_name = self._pseudo_type.__class__.__name__
            raise RuntimeError(f'`{class_name}` is not a subclass of `PseudoPotentialData`.')

        super().__init__(*args, **kwargs)

    @classproperty
    def pseudo_type(cls):  # pylint: disable=no-self-argument
        """Return the pseudo potential type that this family accepts.

        :return: the subclass of ``PseudoPotentialData`` that this family hosts nodes of.
        """
        return cls._pseudo_type

    @classmethod
    def parse_pseudos_from_directory(cls, dirpath):
        """Parse the pseudo potential files in the given directory into a list of data nodes.

        .. note:: The directory pointed to by `dirpath` should only contain pseudo potential files. Optionally, it can
            contain just a single directory, that contains all the pseudo potential files. If any other files are stored
            in the basepath or the subdirectory, that cannot be successfully parsed as pseudo potential files the method
            will raise a `ValueError`.

        :param dirpath: absolute path to a directory containing pseudo potentials.
        :return: list of data nodes
        :raises ValueError: if `dirpath` is not a directory or contains anything other than files.
        :raises ValueError: if `dirpath` contains multiple pseudo potentials for the same element.
        :raises ParsingError: if the constructor of the pseudo type fails for one of the files in the `dirpath`.
        """
        from aiida.common.exceptions import ParsingError

        pseudos = []

        if not os.path.isdir(dirpath):
            raise ValueError(f'`{dirpath}` is not a directory')

        dirpath_contents = os.listdir(dirpath)

        if len(dirpath_contents) == 1 and os.path.isdir(os.path.join(dirpath, dirpath_contents[0])):
            dirpath = os.path.join(dirpath, dirpath_contents[0])

        for filename in os.listdir(dirpath):
            filepath = os.path.join(dirpath, filename)

            if not os.path.isfile(filepath):
                raise ValueError(f'dirpath `{dirpath}` contains at least one entry that is not a file')

            try:
                with open(filepath, 'rb') as handle:
                    pseudo = cls._pseudo_type(handle, filename=filename)
            except ParsingError as exception:
                raise ParsingError(f'failed to parse `{filepath}`: {exception}') from exception
            else:
                if pseudo.element is None:
                    match = re.search(r'^([A-Za-z]{1,2})\.\w+', filename)
                    if match is None:
                        raise ParsingError(
                            f'`{cls._pseudo_type}` constructor did not define the element and could not parse a valid '
                            'element symbol from the filename `{filename}` either. It should have the format '
                            '`ELEMENT.EXTENSION`'
                        )
                    pseudo.element = match.group(1)
                pseudos.append(pseudo)

        if not pseudos:
            raise ValueError(f'no pseudo potentials were parsed from `{dirpath}`')

        elements = set(pseudo.element for pseudo in pseudos)

        if len(pseudos) != len(elements):
            raise ValueError(f'directory `{dirpath}` contains pseudo potentials with duplicate elements')

        return pseudos

    @classmethod
    def create_from_folder(cls, dirpath, label, description='', deduplicate=True):
        """Create a new `PseudoPotentialFamily` from the pseudo potentials contained in a directory.

        :param dirpath: absolute path to the folder containing the UPF files.
        :param label: label to give to the `PseudoPotentialFamily`, should not already exist.
        :param description: description to give to the family.
        :param deduplicate: if True, will scan database for existing pseudo potentials of same type and with the same
            md5 checksum, and use that instead of the parsed one.
        :raises ValueError: if a `PseudoPotentialFamily` already exists with the given name.
        """
        type_check(description, str, allow_none=True)

        try:
            cls.objects.get(label=label)
        except exceptions.NotExistent:
            family = cls(label=label, description=description)
        else:
            raise ValueError(f'the {cls.__name__} `{label}` already exists')

        parsed_pseudos = cls.parse_pseudos_from_directory(dirpath)
        family_pseudos = []

        for pseudo in parsed_pseudos:
            if deduplicate:
                query = QueryBuilder()
                query.append(cls.pseudo_type, subclassing=False, filters={'attributes.md5': pseudo.md5})
                existing = query.first()
                if existing:
                    pseudo = existing[0]
            family_pseudos.append(pseudo)

        # Only store the `Group` and the pseudo nodes now, such that we don't have to worry about the clean up in the
        # case that an exception is raised during creating them.
        family.store()
        family.add_nodes([pseudo.store() for pseudo in family_pseudos])

        return family

    def add_nodes(self, nodes):
        """Add a node or a set of nodes to the family.

        .. note: Each family instance can only contain a single pseudo potential for each element.

        :param nodes: a single `Node` or a list of `Nodes` of type `PseudoPotentialFamily._pseudo_type`. Note that
            subclasses of `_pseudo_type` are not accepted, only instances of that very type.
        :raises ModificationNotAllowed: if the family is not stored.
        :raises TypeError: if nodes are not an instance or list of instance of `PseudoPotentialFamily._pseudo_type`.
        :raises ValueError: if any of the nodes are not stored or their elements already exist in this family.
        """
        if not self.is_stored:
            raise exceptions.ModificationNotAllowed('cannot add nodes to an unstored group')

        if not isinstance(nodes, (list, tuple)):
            nodes = [nodes]

        if any([type(node) is not self._pseudo_type for node in nodes]):  # pylint: disable=unidiomatic-typecheck
            raise TypeError(f'only nodes of type `{self._pseudo_type}` can be added: {nodes}')

        pseudos = {}

        # Check for duplicates before adding any pseudo to the internal cache
        for pseudo in nodes:
            if pseudo.element in self.elements:
                raise ValueError(f'element `{pseudo.element}` already present in this family')
            pseudos[pseudo.element] = pseudo

        self.pseudos.update(pseudos)

        super().add_nodes(nodes)

    @property
    def pseudos(self):
        """Return the dictionary of pseudo potentials of this family indexed on the element symbol.

        :return: dictionary of element symbol mapping pseudo potentials
        """
        if self._pseudos is None:
            self._pseudos = {pseudo.element: pseudo for pseudo in self.nodes}

        return self._pseudos

    @property
    def elements(self):
        """Return the list of elements for which this family defines a pseudo potential.

        :return: list of element symbols
        """
        return list(self.pseudos.keys())

    def get_pseudo(self, element):
        """Return the pseudo potential for the given element.

        :param element: the element for which to return the corresponding pseudo potential.
        :return: pseudo potential instance if it exists
        :raises ValueError: if the family does not contain a pseudo potential for the given element
        """
        try:
            pseudo = self.pseudos[element]
        except KeyError:
            builder = QueryBuilder()
            builder.append(self.__class__, filters={'id': self.pk}, tag='group')
            builder.append(self._pseudo_type, filters={'attributes.element': element}, with_group='group')

            try:
                pseudo = builder.one()[0]
            except exceptions.MultipleObjectsError as exception:
                raise RuntimeError(f'family `{self.label}` contains multiple pseudos for `{element}`') from exception
            except exceptions.NotExistent as exception:
                raise ValueError(
                    f'family `{self.label}` does not contain pseudo for element `{element}`'
                ) from exception
            else:
                self.pseudos[element] = pseudo

        return pseudo

    def get_pseudos(
        self,
        *,
        elements: Union[List[str], Tuple[str]] = None,
        structure: StructureData = None,
    ) -> Mapping[str, StructureData]:
        """Return the mapping of kind names on pseudo potential data nodes for the given list of elements or structure.

        :param elements: list of element symbols.
        :param structure: the ``StructureData`` node.
        :return: dictionary mapping the kind names of a structure on the corresponding pseudo potential data nodes.
        :raises ValueError: if the family does not contain a pseudo for any of the elements of the given structure.
        """
        if elements is not None and structure is not None:
            raise ValueError('cannot specify both keyword arguments `elements` and `structure`.')

        if elements is None and structure is None:
            raise ValueError('have to specify one of the keyword arguments `elements` and `structure`.')

        if elements is not None and not isinstance(elements, (list, tuple)) and not isinstance(elements, StructureData):
            raise ValueError('elements should be a list or tuple of symbols.')

        if structure is not None and not isinstance(structure, StructureData):
            raise ValueError('structure should be a `StructureData` instance.')

        if structure is not None:
            elements = [kind.symbol for kind in structure.kinds]

        return {element: self.get_pseudo(element) for element in elements}
