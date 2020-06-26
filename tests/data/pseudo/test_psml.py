# -*- coding: utf-8 -*-
"""Tests for the :py:`~aiida_pseudo.data.pseudo.psml` module."""
import os

import pytest

from aiida.common.exceptions import ModificationNotAllowed
from aiida_pseudo.data.pseudo import PsmlData


@pytest.mark.usefixtures('clear_db')
def test_constructor(filepath_pseudos):
    """Test the constructor."""
    for filename in os.listdir(filepath_pseudos('psml')):
        with open(os.path.join(filepath_pseudos('psml'), filename), 'rb') as handle:
            pseudo = PsmlData(handle, filename=filename)
            assert isinstance(pseudo, PsmlData)
            assert not pseudo.is_stored
            assert pseudo.element == filename.split('.')[0]


@pytest.mark.usefixtures('clear_db')
def test_set_file(filepath_pseudos, get_pseudo_potential_data):
    """Test the `PsmlData.set_file` method.

    This method allows to change the file, as long as the node has not been stored yet. We need to verify that all the
    information, such as attributes are commensurate when it is stored.
    """
    pseudo = get_pseudo_potential_data(element='Ar', entry_point='psml')
    assert pseudo.element == 'Ar'

    with open(os.path.join(filepath_pseudos('psml'), 'He.psml'), 'rb') as handle:
        pseudo.set_file(handle)
        assert pseudo.element == 'He'

        pseudo.store()
        assert pseudo.element == 'He'

        with pytest.raises(ModificationNotAllowed):
            pseudo.set_file(handle)
