"""Utility functions for writing Bodo tests using tracing."""
# Copyright (C) 2022 Bodo Inc. All rights reserved.
import json
import os
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List

from mpi4py import MPI

import bodo
from bodo.utils import tracing


class TracingContextManager:
    """
    Class to manage enabling tracing and storing the result as a decoded dictionary.
    Implementation is based on this stack overflow post: https://stackoverflow.com/a/62243608
    """

    def __init__(self):
        self._tracing_events = list()
        self._old_trace_dev = os.environ.get("BODO_TRACE_DEV", None)

    def __enter__(self):
        # Enable tracing
        os.environ["BODO_TRACE_DEV"] = "1"
        tracing.start()

    def __exit__(self, type=None, value=None, traceback=None):
        comm = MPI.COMM_WORLD
        # Generate the trace data
        with NamedTemporaryFile() as f:
            # Write the tracing result to the file.
            tracing.dump(f.name)
            tracing_events = None
            if bodo.get_rank() == 0:
                with open(f.name, "r") as g:
                    # Reload the file and decode the data.
                    tracing_events = json.load(g)["traceEvents"]
            self._tracing_events = comm.bcast(tracing_events)
        # Note: Currently this only works with unique events. If you test produces the same
        # event multiple times you will need to modify this code.
        self._event_names = {x["name"]: i for i, x in enumerate(self._tracing_events)}
        # Reset tracing
        if self._old_trace_dev is None:
            del os.environ["BODO_TRACE_DEV"]
        else:
            os.environ["BODO_TRACE_DEV"] = self._old_trace_dev

    @property
    def tracing_events(self) -> List[Dict[str, Any]]:
        """Return the fully list of tracing events.

        Returns:
            List[Dict[str, Any]]: List of json events.
        """
        return self._tracing_events

    def get_event(self, event_name: str) -> Dict[str, Any]:
        """Returns the last event with a given name.

        Args:
            event_name (str): Name of the event to return.

        Raises:
            ValueError: Event does not exist

        Returns:
            Dict[str, Any]: Dictionary containing the event.
        """
        if event_name not in self._event_names:
            raise ValueError(
                f"Event {event_name} not found in tracing. Possible events: {self._event_names.keys()}"
            )
        idx = self._event_names[event_name]
        return self._tracing_events[idx]

    def get_event_attribute(self, event_name: str, attribute_name: str) -> Any:
        """Returns the attribute of the given attribute_name in the last
        event with the given event_name

        Args:
            event_name (str): Name of the event to search.
            attribute_name (attribute_name): Name of the event to return.

        Raises:
            ValueError: Attribute does not exist in the event.

        Returns:
            Any: Attribute in question. Type depends on the attribute.
        """
        event = self.get_event(event_name)
        event_args = event["args"]
        if attribute_name not in event_args:
            raise ValueError(
                f"Attribute {attribute_name} not found in {event_name}. Possible attributes: {event_args.keys()}"
            )
        return event_args[attribute_name]