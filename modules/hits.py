import numpy as np
import numpy.lib.recfunctions
from root_numpy import root2array
from cylinder import CyDet, CTH
from random import Random
from collections import Counter

"""
Notation used below:
 - wire_id is flat enumerator of all wires
 - layer_id is the index of layer
 - wire_index is the index of wire in the layer
"""
def _return_branches_as_list(branches):
    """
    Ensures branches are given as list
    """
    # Deal with requested branches
    if branches is None:
        branches = []
    if not isinstance(branches, list):
        branches = [branches]
    return branches

class FlatHits(object):
    # pylint: disable=too-many-instance-attributes
    # pylint: disable=bad-continuation
    def __init__(self,
                 path,
                 tree='COMETEventsSummary',
                 prefix="CDCHit.f",
                 branches=None,
                 empty_branches=None,
                 hit_type_name="HitType",
                 key_name="EventNumber",
                 use_evt_idx=True,
                 signal_coding=1,
                 finalize_data=True,
                 n_evts=None):
        """
        Dataset provides an interface to work with MC stored in root format.
        Results of methods are either numpy.arrays or scipy.sparse objects.
        Hits are flattened from [event][evt_hits] structure to [all_hits], with
        look up tables between hits and event stored.

        Additionally, all geometry IDs are flattened from [row][id] to [flat_id]

        :param path: path to rootfile
        :param tree: name of the tree in root dataset
        :param branches: branches from root file to import defined for each hit
        :param evt_branches: branches from root file to import defined for each
                             event
        :param hit_type_name: name of the branch that determines hit type
        :param n_hit_name: name of branch that gives the number of hits in each
                           event
        :param signal_coding: value in hit_type_name branch that signifies a
                              signal hit.  Default is 1
        """
        # Assumptions about data naming and signal labelling conventions
        self.prefix = prefix
        self.key_name = self.prefix + key_name
        self.hit_type_name = self.prefix + hit_type_name
        self.signal_coding = signal_coding
        self.n_events = n_evts
        self.use_evt_idx = use_evt_idx
        # Set the number of hits, the number of events, and data to None so that
        # the the next import_root_file knows its the first call
        self.n_hits, self.data = (None, None)

        # Ensure we have a list type of branches
        empty_branches = _return_branches_as_list(empty_branches)
        branches = _return_branches_as_list(branches)
        # Append the prefix if it is not provided
        branches = [self.prefix + branch
                    if not branch.startswith(self.prefix)
                    else branch
                    for branch in branches]
        # Ensure hit type is imported in branches
        branches, empty_branches = self._add_name_to_branches(path, tree,
                                                              self.hit_type_name,
                                                              branches,
                                                              empty_branches)

        # Declare out lookup tables
        self.hits_to_events = None
        self.event_to_hits = None
        self.event_to_n_hits = None
        # Get the number of hits for each event
        self._generate_event_to_n_hits_table(path, tree)
        # Fill the look up tables
        self._generate_lookup_tables()

        # Declare the counters
        self.n_hits = None
        self.n_events = None
        # Fill the counters
        self._generate_counters()

        # Get the hit data we want
        if branches:
            data_columns = self._import_root_file(path, tree=tree,
                                               branches=branches)
        # Default to empty list
        else:
            data_columns = []

        # Label each hit with the number of hits in its event
        all_key_column = self._import_root_file(path, tree=tree,
                                                branches=self.key_name)

        # Index each hit by hit ID and by event index
        self.hits_index_name = self.prefix + "hits_index"
        self.event_index_name = self.prefix + "event_index"
        # Index each hit by hit and event
        event_index_column = self.hits_to_events
        hits_index_column = np.arange(self.n_hits)

        # Zip it all together in a record array
        self.all_branches = branches + [self.key_name] +\
                                       [self.hits_index_name] +\
                                       [self.event_index_name]
        self.data = data_columns + all_key_column + \
                    [hits_index_column] + [event_index_column]

        # Add in the empty branches
        empty_branches = _return_branches_as_list(empty_branches)
        # TODO fix hack
        empty_branches = np.unique(empty_branches)
        for branch in empty_branches:
            self.data += [np.zeros(self.n_hits)]
            self.all_branches += [branch]

        # Finialize the data if this is the final form
        if finalize_data:
            self._finalize_data()

    def _check_for_branches(self, path, tree, branches, soft_check=False):
        """
        This checks for the needed branches before they are imported to avoid
        the program to hang without any error messages

        :param path: path to root file
        :param tree: name of tree in root file
        :param branches: required branches
        """
        # Import one event with all the branches to get the names of the
        # branches
        dummy_root = root2array(path, treename=tree, start=0, stop=1)
        # Get the names of the imported branches
        availible_branches = dummy_root.dtype.names
        # Get the requested branches that are not availible
        bad_branches = list(set(branches) - set(availible_branches))
        bad_request = len(bad_branches) != 0
        # Return false if this is a soft check and these branches are not found
        if soft_check and bad_request:
            return False
        # Otherwise, shut it down if its the wrong length
        elif bad_request:
            # Check that this is zero in length
            assert len(bad_branches) == 0, "ERROR: The requested branches:\n"+\
                    "\n".join(bad_branches) + "\n are not availible\n"+\
                    "The branches availible are:\n"+"\n".join(availible_branches)
        else:
            return True

    def _add_name_to_branches(self, path, tree, name, branches, empty_branches):
        """
        Determine which list of branches to put this variable
        """
        # Check if this file already has one
        has_name = self._check_for_branches(path, tree,
                                               branches=[name],
                                               soft_check=True)
        if has_name:
            branches = _return_branches_as_list(branches)
            branches += [name]
        else:
            empty_branches = _return_branches_as_list(empty_branches)
            empty_branches += [name]
        return branches, empty_branches


    def _import_root_file(self, path, tree, branches):
        """
        This wraps root2array to protect the user from importing non-existant
        branches, which cause the program to hang without any error messages

        :param path: path to root file
        :param tree: name of tree in root file
        :param branches: required branches
        """
        # Ensure branches is a list
        if not isinstance(branches, list):
            branches = [branches]
        # Check the braches we want are there
        _ = self._check_for_branches(path, tree, branches)
        # Grab the branches one by one to save on memory
        data_columns = []
        for branch in branches:
            # Count the number of entries to grab
            if self.use_evt_idx:
                n_entries = sum(self.event_to_n_hits[:self.n_events])
            else:
                n_entries = self.n_events
            # Grab the branch
            event_data = root2array(path, treename=tree, \
                                    branches=[branch],
                                    start=0, stop=n_entries)
            # If we know the number of hits and events, require the branch is as
            # long as one of these
            if (self.n_hits is not None) and (self.n_events is not None):
                # Record the number of entries
                data_length = len(event_data)
                # Deal with variables defined once per event
                if data_length == self.n_events:
                    # Concatonate the branch if it is an array of lists, i.e. if
                    # it is defined for every hit
                    if event_data.dtype[branch] == object:
                        event_data = np.concatenate(event_data[branch])
                        # Check that the right number of hits are defined
                        data_length = len(event_data)
                    # Otherwise assume it is defined event-wise, stretch it by
                    # event so each hit has the value corresponding to its
                    # event.
                    else:
                        # Check the length
                        data_length = len(event_data)
                        event_data = event_data[branch][self.hits_to_events]
                else:
                    event_data = event_data[branch]
                # Check that the length of the array makes sense
                assert (data_length == self.n_hits) or\
                       (data_length == self.n_events),\
                       "ERROR: The length of the data in the requested "+\
                       "branch " + branch + " is not the length of the "+\
                       "number of events or the number of hits"
                # Add this branch
                data_columns.append(event_data)
            # If we do not know the number of hits and events, assume its
            # defined hit-wise
            else:
                data_columns.append(np.concatenate(event_data[branch]))
        # Return
        return data_columns

    def _generate_event_to_n_hits_table(self, path, tree):
        """
        Creates look up tables to map from event index to number of hits from
        the root files
        """
        # Check the branch we need to define the number of hits is there
        _ = self._check_for_branches(path, tree, branches=[self.key_name])
        # Import the data
        event_data = root2array(path, treename=tree,
                                branches=[self.key_name])
        event_data = event_data[self.key_name]
        # Return the number of hits in each event
        if self.use_evt_idx:
            # Check the hits are sorted by event
            assert np.array_equal(event_data, np.sort(event_data)),\
                "Event index named {} not sorted".format(self.key_name)
            # Return the number of hits in each event
            _, event_to_n_hits = np.unique(event_data, return_counts=True)
        else:
            # Assume number of hits per event is stored already
            event_to_n_hits = event_data.copy().astype(int)
        # Trim to the requested number of events
        self.event_to_n_hits = event_to_n_hits[:self.n_events]

    def _generate_lookup_tables(self):
        """
        Generate mappings between hits and events from current event_to_n_hits
        """
        # Build the look up tables
        first_hit = 0
        try:
            hits_to_events = np.zeros(sum(self.event_to_n_hits))
        except ValueError:
            print type(self.event_to_n_hits)
        event_to_hits = []
        for event, n_hits in enumerate(self.event_to_n_hits):
            # Record the last hit in the event
            last_hit = first_hit + n_hits
            # Record the range of hit IDs
            event_to_hits.append(np.arange(first_hit, last_hit))
            # Record the event of each hit
            hits_to_events[first_hit:last_hit] = event
            # Shift to the next event
            first_hit = last_hit
        # Shift the event-to-hit list into a numpy object array
        self.event_to_hits = np.array(event_to_hits)
        # Ensure all indexes in hits to events are integers
        self.hits_to_events = hits_to_events.astype(int)

    def _generate_counters(self):
        """
        Generate the number of events and number of hits
        """
        self.n_hits = len(self.hits_to_events)
        self.n_events = len(self.event_to_n_hits)

    def _generate_indexes(self):
        '''
        Reset the hit and event indexes
        '''
        self.data[self.hits_index_name] = np.arange(self.n_hits)
        self.data[self.event_index_name] = self.hits_to_events

    def _reset_all_internal_data(self):
        """
        Reset all look up tables, indexes, and counts
        """
        # Reset the look up tables from the current event_to_n_hits table
        self._generate_lookup_tables()
        # Set the number of hits and events for this data
        self._generate_counters()
        # Set the indexes
        self._generate_indexes()

    def _reset_event_to_n_hits(self):
        # Find the hits to remove
        self.event_to_n_hits = np.bincount(self.data[self.event_index_name],
                                           minlength=self.n_events)
        # Remove these sums
        assert (self.event_to_n_hits >= 0).all(),\
                "Negative number of hits not allowed!"
        # Trim the look up tables of empty events
        empty_events = np.where(self.event_to_n_hits > 0)[0]
        self._trim_lookup_tables(empty_events)

    def _trim_lookup_tables(self, events):
        """
        Trim the lookup tables to the given event indexes
        """
        # Trim the event indexed tables
        self.event_to_n_hits = self.event_to_n_hits[events]
        # Set the lookup tables
        self._reset_all_internal_data()

    def _finalize_data(self):
        """
        Zip up the data into a rec array if this is the highest level class of
        this instance
        """
        self.data = np.rec.fromarrays(self.data, names=(self.all_branches))
        self._generate_indexes()

    def _get_mask(self, these_hits, variable, values=None, greater_than=None,
                  less_than=None, invert=False):
        """
        Returns the section of the data where the variable equals
        any of the values
        """
        # Default is all true
        mask = np.ones(len(these_hits))
        if not values is None:
            # Switch to a list if a single value is given
            if not isinstance(values, list):
                values = [values]
            this_mask = np.in1d(these_hits[variable], values)
            mask = np.logical_and(mask, this_mask)
        if not greater_than is None:
            this_mask = these_hits[variable] > greater_than
            mask = np.logical_and(mask, this_mask)
        if not less_than is None:
            this_mask = these_hits[variable] < less_than
            mask = np.logical_and(mask, this_mask)
        if invert:
            mask = np.logical_not(mask)
        return mask

    def get_events(self, events=None, unique=True):
        """
        Returns the hits from the given event(s).  Default gets all events

        :param unique: Force each event to only be retrieved once
        """
        # Check if we want all events
        if events is None:
            return self.data
        # Allow for a single event
        if isinstance(events, int):
            evt_hits = self.event_to_hits[events]
        # Otherwise assume it is a list of events.
        else:
            # Ensure we only get each event once
            if unique:
                events = np.unique(events)
            # Get all the hits we want as flat
            evt_hits = np.concatenate([self.event_to_hits[evt]\
                                       for evt in events])
        # Return the data for these events
        return self.data[evt_hits]

    def trim_events(self, events):
        """
        Keep these events in the data
        """
        # TODO have this operate on event_index
        keep_hits = np.concatenate(self.event_to_hits[events])
        keep_hits = keep_hits.astype(int)
        self.data = self.data[keep_hits]
        self._trim_lookup_tables(events)


    def sort_hits(self, variable, ascending=True, reset_index=True):
        """
        Sorts the hits by the given variable inside each event.  By default,
        this is done in acending order and the hit index is reset after sorting.
        """
        # Sort each event internally
        for evt in range(self.n_events):
            # Get the hits to sort
            evt_hits = self.event_to_hits[evt]
            # Get the sort order of the given variable
            sort_order = self.data[evt_hits].argsort(order=variable)
            # Reverse the order if required
            if ascending == False:
                sort_order = sort_order[::-1]
            # Rearrange the hits
            self.data[evt_hits] = self.data[evt_hits][sort_order]
        # Reset the hit index
        if reset_index == True:
            self._generate_indexes()

    def filter_hits(self, these_hits, variable, values=None, greater_than=None,
                    less_than=None, invert=False):
        """
        Returns the section of the data where the variable equals
        any of the values
        """
        mask = self._get_mask(these_hits=these_hits, variable=variable,
                              values=values, greater_than=greater_than,
                              less_than=less_than, invert=invert)
        return these_hits[mask]

    def trim_hits(self, variable, values=None, greater_than=None,
                  less_than=None, invert=False):
        """
        Keep the hits satisfying this criteria
        """
        # Get the relevant hits to keep
        mask = self._get_mask(these_hits=self.data, variable=variable,
                              values=values, greater_than=greater_than,
                              less_than=less_than, invert=invert)
        # Remove the hits
        self.data = self.data[mask]
        self._reset_event_to_n_hits()

    def add_hits(self, hits, event_indexes=None):
        """
        Append the hits to the current data. If event indexes are supplied, then
        the hits are added event-wise to the event indexes provided.  Otherwise,
        they are stacked on top of each event, starting with event 0
        """
        # TODO fix the fact that flathits will break cause no time_name
        self.data = np.hstack([self.data, hits])
        self.data.sort(order=[self.event_index_name, self.time_name])
        self._reset_event_to_n_hits()

    def get_other_hits(self, hits):
        """
        Returns the hits from the same event(s) as the given hit list
        """
        events = self.hits_to_events[hits]
        events = np.unique(events)
        return self.get_events(events)

    def get_signal_hits(self, events=None):
        """
        Returns the hits from the same event(s) as the given hit list.
        Default gets hits from all events.
        """
        # Get the events
        these_hits = self.get_events(events)
        these_hits = self.filter_hits(these_hits, self.hit_type_name,
                                      self.signal_coding)
        return these_hits

    def get_background_hits(self, events=None):
        """
        Returns the hits from the same event(s) as the given hit list
        Default gets hits from all events.
        """
        these_hits = self.get_events(events)
        these_hits = self.filter_hits(these_hits, self.hit_type_name,
                                      self.signal_coding, invert=True)
        return these_hits

    def print_branches(self):
        """
        Print the names of the data available once you are done
        """
        # Print status message
        print "Branches available are:"
        print "\n".join(self.all_branches)

# TODO inheret the MutableSequence attributes of the data directly
#    def __getitem__(self, key)
#    def __setitem__(self, key)
#    def __len__(self, key)

class GeomHits(FlatHits):
    # pylint: disable=too-many-instance-attributes
    # pylint: disable=bad-continuation
    # pylint: disable=relative-import
    # pylint: disable=unbalanced-tuple-unpacking
    def __init__(self,
                 geom,
                 path,
                 tree='COMETEventsSummary',
                 prefix="CDCHit.f",
                 branches=None,
                 empty_branches=None,
                 row_name="layerID",
                 idx_name="cellID",
                 edep_name="Charge",
                 time_name="DetectedTime",
                 flat_name="vol_id",
                 trig_name="TrigTime",
                 finalize_data=True,
                 **kwargs):
        """
        This generates hit data in a structured array from an input root file
        from a file. It assumes that the hits are associated to some geometrical
        structure, which is organized by row and index.

        :param path: path to rootfile
        :param tree: name of the tree in root dataset
        :param branches: branches from root file to import
        :param hit_type_name: name of the branch that determines hit type
        :param n_hit_name: name of branch that gives the number of hits in each
                           event
        :param signal_coding: value in hit_type_name branch that signifies a
                              signal hit
        """
        # Name the trigger data row
        self.trig_name = prefix + trig_name
        # Add trig name to branches
        branches, empty_branches = self._add_name_to_branches(path,
                                                              tree,
                                                              self.trig_name,
                                                              branches,
                                                              empty_branches)
        # Initialize the base class
        FlatHits.__init__(self,
                          path,
                          tree='COMETEventsSummary',
                          branches=branches,
                          empty_branches=empty_branches,
                          finalize_data=False,
                          **kwargs)

        # Get the geometry flat_IDs
        self.row_name = self.prefix + row_name
        self.idx_name = self.prefix + idx_name
        self.flat_name = self.prefix + flat_name

        # Get the geometry of the detector
        self.geom = geom

        # Build the flattened ID row
        geom_column = self._get_geom_flat_ids(path, tree=tree)

        # Add these data to the data list
        self.data.append(geom_column)
        self.all_branches.append(self.flat_name)

        # Define the names of the time and energy depostition columns
        self.edep_name = self.prefix + edep_name
        self.time_name = self.prefix + time_name

        # Import these, noting this will be ignored if they already exist
        edep_column = self._import_root_file(path, tree=tree,
                                             branches=[self.edep_name])
        time_column = self._import_root_file(path, tree=tree,
                                             branches=[self.time_name])

        # Add these data to the data list
        self.data += edep_column
        self.data += time_column
        self.all_branches.append(self.edep_name)
        self.all_branches.append(self.time_name)

        # Finialize the data if this is the final form
        if finalize_data:
            self._finalize_data()

    def _finalize_data(self):
        """
        Zip up the data into a rec array if this is the highest level class of
        this instance and sort by time
        """
        super(GeomHits, self)._finalize_data()
        self.sort_hits(self.time_name)

    def _get_geom_flat_ids(self, path, tree):
        """
        Labels each hit by flattened geometry ID to replace the use of volume
        row and volume index
        """
        # Import the data
        row_data, idx_data = self._import_root_file(path, tree=tree,
                                                    branches=[self.row_name,
                                                              self.idx_name])
        # Flatten the volume names and IDs to flat_voldIDs
        flat_ids = np.zeros(self.n_hits)
        for row, idx, hit in zip(row_data, idx_data, range(self.n_hits)):
            flat_ids[hit] = self.geom.point_lookup[row, idx]
        # Save this column and name it
        flat_id_column = flat_ids.astype(int)
        return flat_id_column

    def get_measurement(self, events, name):
        """
        Returns requested measurement by event

        :return: numpy.array of length self.n_hits
        """
        # Select the relevant event from data
        return self.get_events(events)[name]

    def get_hit_vols(self, events, unique=True, hit_type="both"):
        """
        Returns the sequence of flat_ids that register hits in given event

        :return: numpy array of hit wires
        :param: hit_type defines which hit volumes should be retrieved.
                Possible valuses are both, signal, and background
        """
        # Select the relevant event from data
        hit_type = hit_type.lower()
        assert hit_type.startswith("both") or\
               hit_type.startswith("sig") or\
               hit_type.startswith("back"),\
               "Hit type "+ hit_type+ " selected.  This must be both, signal,"+\
               " or background"
        if hit_type == "both":
            flat_ids = self.get_events(events)[self.flat_name]
        elif hit_type.startswith("sig"):
            flat_ids = self.get_signal_hits(events)[self.flat_name]
        elif hit_type.startswith("back"):
            flat_ids = self.get_background_hits(events)[self.flat_name]
        if unique is True:
            flat_ids = np.unique(flat_ids)
        return flat_ids

    def get_sig_vols(self, events, unique=True):
        """
        Returns the sequence of flat_ids that register signal hits in given
        event

        :return: numpy array of hit wires
        """
        # Select the relevant event from data
        return self.get_hit_vols(events, unique, hit_type="sig")

    def get_bkg_vols(self, events, unique=True):
        """
        Returns the sequence of flat_ids that register hits in given event

        :return: numpy array of hit wires
        """
        # Select the relevant event from data
        return self.get_hit_vols(events, unique, hit_type="back")

    def get_hit_vector(self, events, unique=True):
        """
        Returns a vector denoting whether or not a wire has a hit on it. Returns
        1 for a hit, 0 for no hit

        :return: numpy array of shape [n_wires] whose value is 1 for a hit, 0
                 for no hit
        """
        # Get the flat vol IDs for those with hits
        hit_vols = self.get_hit_vols(events, unique=True)
        # Make the hit vector
        hit_vector = np.zeros(self.geom.n_points)
        hit_vector[hit_vols] = 1
        return hit_vector

    def get_hit_types(self, events, unique=True):
        """
        Returns all hit types, where signal is 1, background is 2,
        nothing is 0.

        :return: numpy.array of shape [CyDet.n_points]
        """
        result = np.zeros(self.n_hits, dtype=int)
        # Get the background hits
        bkg_hits = self.get_background_hits(events)[self.hits_index_name]
        result[bkg_hits] = 2
        # Get the signal hits
        sig_hits = self.get_signal_hits(events)[self.hits_index_name]
        result[sig_hits] = 1
        return result.astype(int)

    def get_energy_deposits(self, events):
        """
        Returns energy deposit in all wires

        :return: numpy.array of shape [CyDet.n_points]
        """
        energy_deposit = self.get_measurement(events, self.edep_name)
        return energy_deposit

    def get_hit_time(self, events):
        """
        Returns the timing of the hit

        :return: numpy.array of shape [CyDet.n_points]
        """
        time_hit = self.get_measurement(events, self.time_name)
        return time_hit

    def get_trigger_time(self, events):
        """
        Returns the timing of the trigger on an event

        :return: numpy.array of shape [CyDet.n_points]
        """
        # Check the trigger time has been set
        assert "CDCHit.fTrigTime" in self.all_branches,\
                "Trigger time has not been set yet"
        return self.get_measurement(events, self.trig_name)

    def get_relative_time(self, events):
        """
        Returns the difference between the start time of the hit and the time of
        the trigger.  This value is capped to the time window of 1170 ns
        :return: numpy array of (t_start_hit - t_trig)%1170
        """
        trig_time = self.get_trigger_time(events)
        hit_time = self.get_hit_time(events)
        return hit_time - trig_time


class CyDetHits(GeomHits):
    # pylint: disable=too-many-instance-attributes
    # pylint: disable=bad-continuation
    # pylint: disable=relative-import
    def __init__(self,
                 path,
                 tree='COMETEventsSummary',
                 prefix="CDCHit.f",
                 chan_name="Channel",
                 finalize_data=True,
                 time_offset=None,
                 **kwargs):
        """
        This generates hit data in a structured array from an input root file
        from a file. It assumes the naming convention "CDCHit.f"+ variable for
        all leaves. It overlays its data on the uses the CyDet class to define
        its geometry.

        :param path: path to rootfile
        :param tree: name of the tree in root dataset
        :param branches: branches from root file to import
        :param hit_type_name: name of the branch that determines hit type
        :param n_hit_name: name of branch that gives the number of hits in each
                           event
        :param signal_coding: value in hit_type_name branch that signifies a
                              signal hit
        """
        # Set the channel name for the flat_ids
        # TODO this is ugly, please clear out the look up table business
        self.chan_name = prefix + chan_name
        # Build the geom hits object
        GeomHits.__init__(self,
                          CyDet(),
                          path,
                          tree='COMETEventsSummary',
                          prefix=prefix,
                          finalize_data=False,
                          **kwargs)

        # Finialize the data if this is the final form
        if finalize_data:
            self._finalize_data()
        self.time_offset = time_offset

    def _get_geom_flat_ids(self, path, tree):
        """
        Labels each hit by flattened geometry ID to replace the use of volume
        row and volume index
        """
        # Check if chan_name is present is the root file
        has_chan = self._check_for_branches(path, tree,
                                            branches=[self.chan_name],
                                            soft_check=True)
        if has_chan:
            return self._import_root_file(path, tree,
                                          branches=[self.chan_name])[0]
        else:
            return super(CyDetHits, self)._get_geom_flat_ids(path, tree)

    def get_measurement(self, name, events=None, shift=None, default=0, 
                        only_hits=True, flatten=False):
        """
        Returns requested measurement in volumes, returning the default value if
        the hit does not register this measurement

        NOTE: IF COINCIDENCE HASN'T BEEN DEALT WITH THE FIRST HIT ON THE CHANNEL
        WILL BE TAKEN.  The order is determined by the hits_index

        :return: numpy.array of shape [len(events), CyDet.n_points]
        """
        # Check if events is empty
        if events is None:
            events = np.arange(self.n_events)
        # Get the events as an array
        my_events = np.array(events)
        # Select the relevant event from data
        meas = self.get_events(my_events)[name]
        # Get the wire_ids and event_ids of the hit data
        wire_ids = self.get_hit_vols(my_events, unique=False)
        # Map the evnt_ids to the minimal continous set
        evnt_ids = np.repeat(np.arange(my_events.size),
                             self.event_to_n_hits[my_events])
        # Add the measurement to the correct cells in the result
        result = default*np.ones((my_events.size, self.geom.n_points),
                                  dtype=float)
        result[evnt_ids, wire_ids] = meas
        if shift is not None:
            result = result[:, self.geom.shift_wires(shift)]
        if only_hits:
            # TODO this will return copies of hits that are preferred.  This is
            # very powerful for dealing with coincidence.  Please revisit to
            # utilize properly
            result = result[evnt_ids, wire_ids]
        if flatten:
            result = result.flatten()
        return result

    def get_hit_types(self, events, unique=True):
        """
        Returns hit type in all volumes, where signal is 1, background is 2,
        nothing is 0.  If signal and background are both incident, signal takes
        priority

        :return: numpy.array of shape [CyDet.n_points]
        """
        result = np.zeros(self.geom.n_points, dtype=int)
        # Get the background hits
        bkg_hits = np.unique(self.get_background_hits(events)[self.flat_name])
        result[bkg_hits] = 2
        # Get the signal hits
        sig_hits = np.unique(self.get_signal_hits(events)[self.flat_name])
        result[sig_hits] = 1
        return result.astype(int)

    def get_hit_wires_even_odd(self, events):
        """
        Returns two sequences of wire_ids that register hits in given event, the
        first is only in even layers, the second is only in odd layers

        :return: numpy array of hit wires
        """
        hit_wires = self.get_hit_vols(events)
        odd_wires = np.where((self.geom.point_pol == 1))[0]
        even_hit_wires = np.setdiff1d(hit_wires, odd_wires, assume_unique=True)
        odd_hit_wires = np.intersect1d(hit_wires, odd_wires, assume_unique=True)
        return even_hit_wires, odd_hit_wires

    def get_hit_vector_even_odd(self, events):
        """
        Returns a vector denoting whether or not a wire on an odd layer has a
        hit on it. Returns 1 for a hit in an odd layer, 0 for no hit and all
        even layers

        :return: numpy array of shape [n_wires] whose value is 1 for a hit on an
                odd layer, 0 otherwise
        """
        even_wires, odd_wires = self.get_hit_wires_even_odd(events)
        even_hit_vector = np.zeros(self.geom.n_points)
        even_hit_vector[even_wires] = 1
        odd_hit_vector = np.zeros(self.geom.n_points)
        odd_hit_vector[odd_wires] = 1
        return even_hit_vector, odd_hit_vector

### DEPRECIATED METHODS INCLUDED FOR BACKWARDS COMPATIBILITY ###

    def get_sig_wires(self, events):
        """
        Get all the signal wires in a given event.  This method is depreciated
        and simply wraps get_sig_vols
        """
        return self.get_sig_vols(events)

    def get_bkg_wires(self, events):
        """
        Get all the background wires in a given event.  This method is
        depreciated and simply wraps get_bkg_vols
        """
        return self.get_bkg_vols(events)

    def get_hit_wires(self, events):
        """
        Get all the hit wires in a given event.  This method is depreciated
        and simply wraps get_hit_vols
        """
        return self.get_hit_vols(events)

class CTHHits(GeomHits):
    # pylint: disable=too-many-instance-attributes
    # pylint: disable=bad-continuation
    # pylint: disable=relative-import
    def __init__(self,
                 path,
                 tree='COMETEventsSummary',
                 prefix="CTHHits.f",
                 finalize_data=True,
                 **kwargs):
        """
        This generates hit data in a structured array from an input root file
        from a file. It assumes the naming convention "M_"+ variable for
        all leaves. It overlays its data on the uses the CTH class to define
        its geometry.

        :param path: path to rootfile
        :param tree: name of the tree in root dataset
        :param branches: branches from root file to import
        :param hit_type_name: name of the branch that determines hit type
        :param n_hit_name: name of branch that gives the number of hits in each
                           event
        :param signal_coding: value in hit_type_name branch that signifies a
                              signal hit
        """
        GeomHits.__init__(self,
                          CTH(),
                          path,
                          tree='COMETEventsSummary',
                          prefix=prefix,
                          finalize_data=False,
                          **kwargs)

        # Add labels for upstream and downstream CTH setups
        z_pos_column = self._get_geom_z_pos(path, tree)
        self.data += [z_pos_column[0]]
        self.z_pos_name = self.prefix + "position"
        self.all_branches.append(self.z_pos_name)

        # Initialize the up and down stream data holders
        self.up_data, self.down_data = None, None

        if finalize_data:
            self._finalize_data()

    def _finalize_data(self):
        """
        Zip up the data into a rec array if this is the highest level class of
        this instance and sort by time
        """
        super(CTHHits, self)._finalize_data()
        # Remove passive volumes from the hit data
        # TODO fix passive volume hack
        self.trim_hits(variable=self.flat_name, values=range(0, 64*2))
        # Shortcut the upstream and downstream sections
        self.up_data = self.filter_hits(self.data, self.z_pos_name, 1)
        self.down_data = self.filter_hits(self.data, self.z_pos_name, 0)

    def _get_geom_flat_ids(self, path, tree):
        """
        Labels each hit by flattened geometry ID to replace the use of volume
        row and volume index
        """
        # Import the data
        row_data, idx_data = self._import_root_file(path, tree=tree,
                                                    branches=[self.row_name,
                                                              self.idx_name])
        # Pull out the names of the volumes, removing the tag
        for tag in ['U', 'D']:
            row_data = np.char.rstrip(row_data.astype(str), tag)
        # Map from volume names to row indexes
        row_data = np.vectorize(self.geom.name_to_row.get)(row_data)
        # Flatten the volume names and IDs to flat_voldIDs
        flat_ids = np.zeros(self.n_hits)
        for row, idx, hit in zip(row_data, idx_data, range(self.n_hits)):
            flat_ids[hit] = self.geom.point_lookup[row, idx]
        # Save this column and name it
        flat_id_column = flat_ids.astype(int)
        return flat_id_column

    def _get_geom_z_pos(self, path, tree):
        """
        Labels each hit by if it a part of the upstream or downstream hodoscope
        """
        # Import the data
        z_pos_data = self._import_root_file(path, tree=tree,
                                            branches=[self.row_name])
        # Strip the volume names to tag the data as upstream or downstream
        z_pos_data = np.array(z_pos_data).astype(str)
        # Start with the active ones
        for vol in self.geom.active_names + self.geom.passive_names:
            z_pos_data = np.char.lstrip(z_pos_data.astype(str), vol)
        # Move to the passive ones
        z_pos_data = np.vectorize(self.geom.pos_to_col.get)(z_pos_data)
        return z_pos_data

    def get_events(self, events=None, unique=True, hodoscope="both"):
        """
        Returns the hits from the given event(s).  Default gets all events

        :param unique: Force each event to only be retrieved once
        """
        assert hodoscope.startswith("both") or\
               hodoscope.startswith("up") or\
               hodoscope.startswith("down"),\
               "Hodoscope "+ hodoscope +" selected.  This must be both, "+\
               " upstream, or downstream"
        events = super(self.__class__, self).get_events(events)
        if hodoscope.startswith("up"):
            events = self.filter_hits(events, self.z_pos_name, 1)
        elif hodoscope.startswith("down"):
            events = self.filter_hits(events, self.z_pos_name, 0)
        return events

class CDCHits(FlatHits):
    # pylint: disable=too-many-instance-attributes
    # pylint: disable=bad-continuation
    # pylint: disable=relative-import
    def __init__(self,
                 cydet_hits,
                 cth_hits):
        """
        A class to support overlaying hit classes of the same type.  This will
        returned the combined event from each of the underlying hit classes.

        """
        # TODO assertion here
        self.cth = cth_hits
        self.cydet = cydet_hits
        self.n_events = min(self.cydet.n_events, self.cth.n_events)

    def print_branches(self):
        """
        Print the names of the data available once you are done
        """
        # Print status message
        print "CTH Branches:"
        self.cth.print_branches()
        print "CyDet Branches:"
        self.cydet.print_branches()

    def trim_events(self, events):
        """
        Remove these events from the data
        """
        self.cydet.trim_events(events)
        self.cth.trim_events(events)
        self.n_events = self.cydet.n_events

    def apply_timing_cut(self, cth_lower=700, cth_upper=110,
                               cydet_lower=700, cydet_upper=1620):
        """
        Remove the hits that do not pass timing cut
        """
        self.cth.trim_hits(variable=self.cth.time_name,\
                           less_than=1100, greater_than=700)
        self.cydet.trim_hits(variable=self.cydet.time_name,\
                           less_than=1620, greater_than=700)

    def apply_cth_trigger(self):
        """
        Remove events that do not hit the CTH trigger
        """
        trigger_events = []
        for evt in range(self.n_events):
            sig_hits = self.cth.get_signal_hits(evt)
            if len(sig_hits) != 0:
                trigger_events.append(evt)
        trigger_events = np.array(trigger_events)
        self.trim_events(trigger_events)

    def apply_n_hits_cut(self, n_hits=30):
        """
        Remove events that do not have enough CyDet hits
        """
        n_signal_hits = np.array([len(self.cydet.get_signal_hits(evt))
                                 for evt in range(self.cydet.n_events)])
        n_signal_hits = np.array(n_signal_hits)
        good_n_hits = np.where(n_signal_hits >= n_hits)[0]
        self.trim_events(good_n_hits)

    def apply_max_layer_cut(self, ideal_layer=5):
        """
        Remove events that do not have enough CyDet hits
        """
        max_layer = []
        for evt in range(self.n_events):
            sig_wires = self.cydet.get_sig_wires(evt)
            these_layers = self.cydet.geom.point_layers[sig_wires]
            if len(sig_wires) != 0:
                max_layer.append(np.max(these_layers))
            else:
                max_layer.append(-1)
        max_layer = np.array(max_layer)
        good_max_layer = np.where(max_layer >= ideal_layer)[0]
        self.trim_events(good_max_layer)

class HitsMerger(CyDetHits):
    # pylint: disable=too-many-instance-attributes
    # pylint: disable=bad-continuation
    # pylint: disable=relative-import
    def __init__(self,
                 sig_class,
                 path,
                 tree,
                 this_class="cydet",
                 force_time_accept=True):
        """
        A class to support overlaying hit classes of the same type.  This will
        returned the combined event from each of the underlying hit classes.

        """
        CyDetHits.__init__(self,
                           path=path,
                           tree=tree)


        # Ensure they are the same type of hits class
#        assert isinstance(sig_class, bck_class.__class__),\
#               "The two merged hits must be the same class\n"+\
#               "First Class : {}\n".format(sig_class)+\
#               "Second Class : {}\n".format(bck_class)
#        # Ensure they have the same geometry setup
#        assert sig_class.geom == bck_class.geom,\
#               "The two merged hits must have the same geometry"
        # Remember which one is which
        self.sig_class = sig_class
        self.this_class = this_class

        self.summed_properties = [sig_class.edep_name]
        self.minned_properties = [sig_class.time_name]
        self.early_properties = [sig_class.hit_type_name]

        # Force signal tracks to be in time acceptance window
  #      if force_time_accept:
  #          for evt in range(self.sig_class.n_events):
  #              sig_time = self.sig_class.get_hit_time(evt)
  #              self.sig_class.data[self.sig_class.time_name]

    def get_measurement(self, event, name):
        sig_measure = self.sig_class.get_measurement(event, name)
        bkg_measure = super(CyDetHits, self).get_measurement(event, name)
        if self.this_class=="cth":
            return np.append(bkg_measure, sig_measure)
        if self.this_class=="cydet":
            if name in self.summed_properties:
                return sig_measure + bkg_measure
            if name in self.minned_properties:
                return np.amin(sig_measure, bkg_measure)
            if name in self.early_properties:
                sig_time = sig_class.get_hit_time(event)
                bkg_time = super(HitsMerger,self).get_hit_time(event)
                sig_mask = np.less(sig, bkg)
                bkg_mask = np.logical_not(mask)
                return sig_measure[sig_mask] + bkg_measure[bkg_mask]
