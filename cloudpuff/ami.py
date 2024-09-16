"""AMI creation."""

from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

import boto3

if TYPE_CHECKING:
    from mypy_boto3_ec2.client import EC2Client
    from mypy_boto3_ec2.literals import ImageStateType
    from mypy_boto3_ec2.type_defs import ImageTypeDef


class PendingAMI:
    """A pending AMI creation.

    This is used by AMICreator to keep track of any pending AMI creations,
    in order to determine when completion has finished.
    """

    ######################
    # Instance variables #
    ######################

    #: The AMI creator managing this pending AMI.
    creator: AMICreator

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
        self.creator = creator
        self.id = ami_id

    @property
    def state(self) -> Optional[ImageStateType]:
        """Return the state of the creation.

        Every time this is called, the state will be re-fetched from the
        server.
        """
        results = self.creator.cnx.describe_images(ImageIds=[self.id])
        return results['Images'][0].get('State')


class AMICreator:
    """Manages the creation of AMIs.

    Multiple AMIs can be created in parallel, and the status of the
    creations can be checked through the ``pending`` property.

    This will connect to EC2 using local AWS credentials. The credentials
    profile name can be specified using the :envvar:`CLOUDPUFF_AWS_PROFILE`
    environment variable.
    """

    ######################
    # Instance variables #
    ######################

    #: The EC2 client connection.
    cnx: EC2Client

    #: The list of pending AMIs.
    pending_amis: list[PendingAMI]

    def __init__(
        self,
        *,
        region: str,
    ) -> None:
        """Initialize the AMI creator.

        Args:
            region (str):
                The AWS region to connect to.
        """
        session = boto3.Session(
            profile_name=os.environ.get('CLOUDPUFF_AWS_PROFILE'))
        self.cnx = session.client('ec2', region_name=region)
        self.pending_amis = []

    def create_ami(
        self,
        *,
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
        result = self.cnx.create_image(InstanceId=instance_id,
                                       Name=name,
                                       Description=description)
        pending_ami = PendingAMI(creator=self,
                                 ami_id=result['ImageId'])
        self.pending_amis.append(pending_ami)

        return pending_ami

    @property
    def pending(self) -> bool:
        """Return whether any AMI creations are still pending."""
        for pending_ami in self.pending_amis:
            if pending_ami.state == 'pending':
                return True

        return False
