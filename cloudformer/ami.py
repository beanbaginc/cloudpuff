from __future__ import unicode_literals

import boto.ec2


class PendingAMI(object):
    """A pending AMI creation.

    This is used by AMICreator to keep track of any pending AMI creations,
    in order to determine when completion has finished.
    """

    def __init__(self, creator, ami_id):
        self.id = ami_id
        self.ami = creator.cnx.get_all_images([ami_id])[0]

    @property
    def state(self):
        """Return the state of the creation.

        Every time this is called, the state will be re-fetched from the
        server.
        """
        self.ami.update()

        return self.ami.state


class AMICreator(object):
    """Manages the creation of AMIs.

    Multiple AMIs can be created in parallel, and the status of the
    creations can be checked through the ``pending`` property.
    """

    def __init__(self, region):
        self.cnx = boto.ec2.connect_to_region(region)
        self.pending_amis = []
        self.created_amis = []

    def create_ami(self, ami_name, instance_id, ami_description):
        """Create an AMI for an instance with the given information.

        The AMI creation will be tracked. A PendingAMI will be stored and
        returned.
        """
        ami_id = self.cnx.create_image(instance_id, ami_name, ami_description)
        pending_ami = PendingAMI(self, ami_id)
        self.pending_amis.append(pending_ami)

        return pending_ami

    @property
    def pending(self):
        """Return whether any AMI creations are still pending."""
        for pending_ami in self.pending_amis:
            if pending_ami.state == 'pending':
                return True

        return False
