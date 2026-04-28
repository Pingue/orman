from django.db import models


class InstrumentFamily(models.Model):
    name = models.CharField(max_length=200)
    def __str__(self):
        return self.name

class Instrument(models.Model):
    name = models.CharField(max_length=200)
    family = models.ForeignKey(InstrumentFamily, on_delete=models.PROTECT)
    def __str__(self):
        return self.name

class Person(models.Model):
    firstNames = models.CharField(max_length=200)
    lastName = models.CharField(max_length=200)
    email = models.CharField(max_length=200)
    phone = models.CharField(max_length=200)
    instruments = models.ManyToManyField(Instrument)
    member = models.BooleanField(default=False)
    def __str__(self):
        return self.firstNames + " " + self.lastName

class Venue(models.Model):
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=200)
    postcode = models.CharField(max_length=200)
    contactName = models.CharField(max_length=200)
    phone = models.CharField(max_length=200)
    email = models.CharField(max_length=200)
    website = models.CharField(max_length=200)
    directions = models.TextField()
    def __str__(self):
        return self.name

class RentalContract(models.Model):
    description = models.TextField()
    supplier = models.CharField(max_length=200)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    startDate = models.DateField()
    endDate = models.DateField()
    def __str__(self):
        return self.description

class MusicItem(models.Model):
    name = models.CharField(max_length=200)
    composer = models.CharField(max_length=200)
    duration = models.DurationField()
    parts = models.ManyToManyField(Instrument, through='MusicItemPart')
    notes = models.TextField()
    contract = models.ForeignKey(RentalContract, on_delete=models.PROTECT, blank=True, null=True)
    def __str__(self):
        return self.name

class MusicItemPart(models.Model):
    musicItem = models.ForeignKey(MusicItem, on_delete=models.PROTECT)
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT)
    file = models.CharField(max_length=200)
    desk = models.IntegerField()    
    def __str__(self):
        return self.musicItem.name + " - " + self.instrument.name

class Rehearsal(models.Model):
    name = models.CharField(max_length=200)
    startDate = models.DateField()
    startTime = models.TimeField()
    endTime = models.TimeField()
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT)
    rehearsalItems = models.ManyToManyField(MusicItem, through='RehearsalItem')
    def __str__(self):
        return self.name

class Performance(models.Model):
    name = models.CharField(max_length=200)
    date = models.DateField()
    time = models.TimeField()
    publicDescription = models.TextField()
    privateDescription = models.TextField()
    published = models.BooleanField(default=False)
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT)
    performanceItems = models.ManyToManyField(MusicItem, through='PerformanceItem')
    def __str__(self):
        return self.name

class RehearsalItem(models.Model):
    rehearsal = models.ForeignKey(Rehearsal, on_delete=models.PROTECT)
    musicItem = models.ForeignKey(MusicItem, on_delete=models.PROTECT)
    order = models.IntegerField()
    def __str__(self):
        return self.rehearsal.name + " - " + self.musicItem.name

class PerformanceItem(models.Model):
    performance = models.ForeignKey(Performance, on_delete=models.PROTECT)
    musicItem = models.ForeignKey(MusicItem, on_delete=models.PROTECT)
    order = models.IntegerField()
    def __str__(self):
        return self.performance.name + " - " + self.musicItem.name

class PerformanceSeats(models.Model):
    performance = models.ForeignKey(Performance, on_delete=models.PROTECT)
    person = models.ForeignKey(Person, on_delete=models.PROTECT)
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT)
    def __str__(self):
        return self.performance.name + " - " + self.person.firstNames + " " + self.person.lastName + " - " + self.instrument.name
# TODO: Assign music parts to people