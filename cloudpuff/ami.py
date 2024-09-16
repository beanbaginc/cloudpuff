"""AMI creation."""

from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

import boto.ec2


class PendingAMI:
    """A pending AMI creation.

    This is used by AMICreator to keep track of any pending AMI creations,
    in order to determine when completion has finished.
    """

    ######################
    # Instance variables #
    ######################

    #: The ID of the pending AMI.
    id: str

    def __init__(
        self,
        *,
        creator: AMICreator,
        ami_id: str,
    ) -> None:
        """Initialize the pending AMI.

        Args:
            creator (AMICreator):
                The AMI creator managing this pending AMI.

            ami_id (str):
                The ID of the pending AMI.
        """
        self.id = ami_id
        self.ami = creator.cnx.get_all_images([ami_id])[0]

    @property
    def state(self) -> str:
        """Return the state of the creation.

        Every time this is called, the state will be re-fetched from the
        server.
        """
        self.ami.update()

        return self.ami.state


class AMICreator:
    """Manages the creation of AMIs.

    Multiple AMIs can be created in parallel, and the status of the
    creations can be checked through the ``pending`` property.
    """

    ######################
    # Instance variables #
    ######################

    #: The list of pending AMIs.
    pending_amis: list[PendingAMI]

    def __init__(
        self,
        region: str,
    ) -> None:
        """Initialize the AMI creator.

        Args:
            region (str):
                The AWS region to connect to.
        """
        self.cnx = boto.ec2.connect_to_region(region)
        self.pending_amis = []

    def create_ami(
        self,
        instance_id: str,
        name: str,
        description: str,
    ) -> PendingAMI:
        """Create an AMI for an instance with the given information.

        The AMI creation will be tracked. A PendingAMI will be stored and
        returned.

        Args:
            instance_id (str):
                The EC2 instance ID to create the AMI from.

            name (str):
                The new name of the AMI.

            description (str):
                The description for the AMI.

        Returns:
            PendingAMI:
            The pending AMI instance.
        """
        ami_id = self.cnx.create_image(instance_id, ami_name, ami_description)
        pending_ami = PendingAMI(creator=self,
                                 ami_id=ami_id)
        self.pending_amis.append(pending_ami)

        return pending_ami

    @property
    def pending(self) -> bool:
        """Return whether any AMI creations are still pending."""
        for pending_ami in self.pending_amis:
            if pending_ami.state == 'pending':
                return True

        return False
