from OnlineArticleRecommendation.Core.synthetic_user import *
from OnlineArticleRecommendation.Utils.weighted_beta_distribution import WeightedBetaDistribution
import scipy.optimize as opt
import time as time
from OnlineArticleRecommendation.Core.ads_news import News, Ad
import numpy as np
from pulp import *
from tqdm import tqdm
import matplotlib.pyplot as plt
import random
import warnings


class NewsLearner:

    def __init__(self, categories, real_slot_promenances=[1.0, 1.0, 1.0, 1.0, 1.0], news_column_pivot=[0.01, 1],
                 news_row_pivot=[1], allocation_approach="standard",
                 allocation_diversity_bounds=(0.1, 0.1, 0.1, 0.1, 0.1, 0.1), lp_rand_technique="rand_1",
                 ads_real_slot_promenances=[1.0, 1.0], ads_allocation=True, maximize_for_bids=False,
                 other_classes_learners=[], ads_allocation_approach="wpdda", ads_allocation_technique="LP"):
        """
        Initialize a news learner. The procedure comprises:

            - Create the matrices for each LP formulation needed to solve allocation problems (Ads and Articles)
            - Split ads according to their categories and keep them in different sets to access them faster
            - Instantiate an Agent for each cell of a (news_column_pivot + 1, news_row_pivot + 1) grid with the following
            shape:

                   Non-Clicked       Clicked
                __________________________________
               |                |                 |
               |                |                 | sum(promincences) = 0
               |                |                 |
               |________________|_________________|
               |                |                 |
               |                |                 | sum(prominences) between 0 and 2
               |                |                 |
               |________________|_________________|
               |                |                 |
               |                |                 | sum(prominences) greater than 2
               |                |                 |
               |________________|_________________|

              each cell contains articles in that state for the current user. News_rows_pivot and News_column_pivot
              are the splitting points between each row and each column.

        :param categories: 1D List. The categories of the articles handled by this Agent. (i.e. Gossip, Sport, etc.)
        :param real_slot_promenances: 1D List. The slot prominences of each slot in the page that will be allocated by
        this Agent. The number of element in this list define the number of slots in the final page and their probabilities
        to be observed.
        :param news_column_pivot: 1D-List. The split-points of columns of a grid used to separate news according to
        their state (see documentation)
        :param news_row_pivot: 1D-List. The split-points of rows of a grid used to separate news according to
        their state (see documentation)
        :param allocation_approach: "standard", "LP", "alt_LP", "full".
        -standard: Allocate articles from the mst promising one to the least promising one, following a decreasing order
        of the slots' prominences
        -LP: use a linear programming formulation linear in the number of categories and quadratic in the number of
        slots to optimize the final page allocation. Incentivize page heterogeneity according to allocation_diversity_bounds
        parameters
        -alt_LP: Not recommended, sub-optimal pages may be generated. Very fast linear programming formulation. Condense cells
        of the articles greed to speed up the process of page allocation.
        -full: Not recommended, very complex linear programming formulation. The number of variables is huge
        :param lp_rand_technique: "Rand_1", "Rand_2", "Rand_3". Technique used to convert the continuous solution of
        Linear programming formulations to an integer solution. They are basically equivalent.
        :param ads_real_slot_promenances: 1D List. The slot prominences in the page for articles allocation. The number
        of element in this list define the number of slots in the final page for the ads and their probabilities
        to be observed.
        :param ads_allocation: True if the Agent should allocate also the ads on the page. False otherwise
        :param maximize_for_bids: True if bids relative to ads should be considerated by the Agent during optimization
        :param other_classes_learners: 1D List of NewsLearner objects. Learner associated to other classes of users
        used to speculate for advertising goals
        :param ads_allocation_approach: "LP", "res_LP" The linear proramming formulation associated to the advertising
        problem.
        -"LP": Integer linear programming formulation linear in the categories and quadratic in the number of ads slots.
        -"res_LP": Restrincted LP, very recommended. Integer linear prog. formulation linear in both categories and
        slots
        :param ads_allocation_technique: "greedy", "wpdda", "pdda".
        -greedy: allocates each ads found by the corresponding linear programming formulation. The resulting number of
        ads on the page will be the number of ads-slots defined
        -pdda: allocates each ads found by the linear programming formulation with probability 0.5.
        -wpdda: allocates each ads found by the linear programming formulation with probability proportional to
        each ad quality w.r.t. the quality provided by the Agents in the other classes learner list.
        """
        total_sum_of_bounds = 0
        for bound in allocation_diversity_bounds:
            if bound < 0:
                raise RuntimeError("Allocation diversity bounds cannot be negative since they express a portion of"
                                   " the total prominence of a page")
            else:
                total_sum_of_bounds += bound
        if total_sum_of_bounds > sum(real_slot_promenances):
            raise RuntimeError("The total sum of the allocation diversity bounds cannot exceed the total sum of the"
                               " real slot prominence.")
        if len(allocation_diversity_bounds) != len(categories):
            warnings.warn("The bounds on categories provided do not match the number of categories. The bounds"
                          " will be extended or truncated to match the number of provided categories")
            if len(allocation_diversity_bounds) < len(categories):
                gap = len(categories) - len(allocation_diversity_bounds)
                allocation_diversity_bounds = list(allocation_diversity_bounds) + [allocation_diversity_bounds[-1]] * gap
            else:
                allocation_diversity_bounds = allocation_diversity_bounds[0:len(categories)]

        for prominence in real_slot_promenances + ads_real_slot_promenances:
            if (prominence > 1) or (prominence < 0):
                raise RuntimeError("Slots' prominence cannot be less than zero or greater than one")

        self.categories = categories  # params to be learnt, the categories of news and ads
        self.last_proposal_weights = np.ones(len(self.categories))  # used to speed up the process of rejecton sampl.
        self.multiple_arms_avg_reward = []  # here we store the values of each pulled arm, for the regret plot
        self.news_pool = []  # all available news are kept here
        self.ads_pool = []  # all available ads are kept here
        self.ads_per_category = []
        self.news_per_category = [0] * len(self.categories)
        self.ads_per_category_cardinality = []
        for _ in range(len(self.categories)):
            self.ads_per_category.append([])
            self.ads_per_category[-1].append([])
            self.ads_per_category[-1].append([])

        self.click_per_page = []
        self.total_ads_clicks_and_displays = []
        self.ads_allocation_approach = ads_allocation_approach
        self.news_times = []
        self.ads_times = []
        self.layout_slots = len(real_slot_promenances)  # the number of slots of a single page
        self.ads_slots = len(ads_real_slot_promenances)
        self.ads_real_slot_promenances = ads_real_slot_promenances
        self.real_slot_promenances = real_slot_promenances  # The real values of slot promenance
        # The number of times we assigned category k to slot i
        self.category_per_slot_assignment_count = np.zeros([len(self.categories), self.layout_slots])
        # The number of times we observed a positive reward for category k allocated in slot i
        self.category_per_slot_reward_count = np.zeros([len(self.categories), self.layout_slots])
        self.other_classes_learners = other_classes_learners
        self.ads_allocation_technique = ads_allocation_technique
        self.weighted_betas_matrix = []
        self.news_row_pivots = news_row_pivot
        self.news_column_pivots = news_column_pivot
        for _ in range(len(news_row_pivot) + 1):
            row = []
            for _ in range(len(news_column_pivot) + 1):
                row.append(WeightedBetaDistribution(self.categories,
                                                    self.layout_slots,
                                                    self.real_slot_promenances))
            self.weighted_betas_matrix.append(row.copy())

        self.bins_for_position = []
        for _ in range(len(self.categories)):
            for x in range(len(self.news_row_pivots) + 1):
                for y in range(len(self.news_column_pivots) + 1):
                    if not ((y == 0) and (x > 0)):
                        self.bins_for_position.append([x, y])
        self.ads_weighted_beta = WeightedBetaDistribution(categories=self.categories,
                                                          layout_slots=self.ads_slots,
                                                          real_slot_promenances=self.ads_real_slot_promenances)

        # Linear programming attributes for NEWS ALLOCATION
        self.allocation_approach = allocation_approach
        self.A = []
        self.B = list(-1 * np.array(allocation_diversity_bounds)) + [1] * (self.layout_slots + len(self.categories) * self.layout_slots)
        self.bounds = [(0, 1)] * len(self.categories) * self.layout_slots * self.layout_slots
        self.lambdas = []
        self.C = []
        self.rand_1_errors = []
        self.rand_2_errors = []
        self.rand_3_errors = []
        self.lp_rand_tech = lp_rand_technique
        """
        In the following, the proper initializations for the matrices and the vectors for the LP approach (news) are done.
        Only a small subset of news will be considered to be allocated with the linear problem. In particular, the 
        number of considered news is (num_of_category) * (num_of_slots_of_a_page)
        """
        for _ in range(len(self.categories) * self.layout_slots):
            self.lambdas += list(np.array(self.real_slot_promenances) * -1)

        # Category constraints creation and insertion into matrix A
        category_count = 0
        for i in range(len(self.categories)):
            row = [0] * (len(self.categories) * self.layout_slots * self.layout_slots - 1)
            row_slot_promenances = []
            for _ in range(self.layout_slots):
                row_slot_promenances += self.real_slot_promenances
            row_slot_promenances = np.array(row_slot_promenances)
            tmp_row = [-1] * self.layout_slots * self.layout_slots
            tmp_row = list(row_slot_promenances * tmp_row)
            row[category_count: category_count + self.layout_slots * self.layout_slots - 1] = tmp_row
            self.A.append(row.copy())
            category_count += self.layout_slots * self.layout_slots

        # Slots' capacity constraints creation and insertion into matrix A
        for i in range(self.layout_slots):
            row = [0] * (len(self.categories) * self.layout_slots * self.layout_slots)
            target_index = i
            while target_index < len(row):
                row[target_index] = 1
                target_index += self.layout_slots

            self.A.append(row.copy())

        # Variables' capacity constraints creation and insertion into matrix A
        initial_index = 0
        for _ in range(len(self.categories) * self.layout_slots):
            row = [0] * (len(self.categories) * self.layout_slots * self.layout_slots)
            row[initial_index:initial_index + self.layout_slots] = [1] * self.layout_slots
            self.A.append(row.copy())
            initial_index += self.layout_slots

        # Linear programming attributes for ADS ALLOCATION
        self.ads_allocation = ads_allocation
        self.ads_lambdas = []
        self.ads_A = []
        self.ads_B = ([1] * self.ads_slots) + ([1] * len(self.categories) * self.ads_slots) + \
                     ([0] * len(self.categories) * self.ads_slots)
        self.ads_C = []
        self.M = 1000  # Big constant value used in the ILP resolution
        self.maximize_for_bids = maximize_for_bids

        """
        In the following, the proper initializations for the matrices and the vectors for the LP approach (ads) are done.
        Only a small subset of ads will be considered to be allocated with the integer linear problem. In particular, the 
        number of considered ads is (num_of_category) * (num_of_ads_slots_of_a_page)
        """
        for _ in range(len(self.categories) * self.ads_slots):
            self.ads_lambdas += self.ads_real_slot_promenances

        # Slots' capacity constraints creation and insertion into matrix ads_A
        for i in range(self.ads_slots):
            row = [0] * (len(self.categories) * self.ads_slots * self.ads_slots)
            target_index = i
            while target_index < len(row):
                row[target_index] = 1
                target_index += self.ads_slots

            self.ads_A.append(row.copy())

        # Variables' capacity constraints creation and insertion into matrix ads_A
        initial_index = 0
        for _ in range(len(self.categories) * self.ads_slots):
            row = [0] * (len(self.categories) * self.ads_slots * self.ads_slots)
            row[initial_index:initial_index + self.ads_slots] = [1] * self.ads_slots
            self.ads_A.append(row.copy())
            initial_index += self.ads_slots

        # Competitors exclusion constraints creation and insertion into matrix ads_A.
        initial_index = 0
        var_initial_index = 0
        for _ in range(len(self.categories)):
            for _ in range(self.ads_slots):
                row = [0] * len(self.categories) * self.ads_slots * self.ads_slots
                row[initial_index:initial_index + self.ads_slots * self.ads_slots] = [1] * self.ads_slots * self.ads_slots
                row[var_initial_index:var_initial_index + self.ads_slots] = [self.M] * self.ads_slots
                var_initial_index += self.ads_slots
                self.ads_A.append(row.copy())
            initial_index += self.ads_slots * self.ads_slots

        # ALTERNATIVE APPROACH TO SOLVE NEWS LP
        self.alt_LP_variables = []
        for cat in range(len(self.categories)):
            for x in range(len(self.news_row_pivots) + 1):
                for y in range(len(self.news_column_pivots) + 1):
                    if (y == 0) and (x != 0):
                        continue
                    for slot in range(self.layout_slots):
                        self.alt_LP_variables.append(LpVariable(name=str(cat) + "_" + str(x) + "_" + str(y) + "_" +
                                                                str(slot),
                                                                lowBound=0,
                                                                cat="Continuous"))

        self.num_of_bins = len(self.weighted_betas_matrix) * len(self.weighted_betas_matrix[0]) - \
                           (len(self.weighted_betas_matrix) - 1)
        self.alt_A = []
        self.alt_B = [0] * self.num_of_bins * len(self.categories) + [1] * self.layout_slots + list(np.array(allocation_diversity_bounds) * -1)
        self.alt_lambdas = []
        self.alt_C = []

        for _ in range(self.num_of_bins * len(self.categories)):
            self.alt_lambdas += self.real_slot_promenances

        # BINS capacity constaints creation and insertion into alt_A (right side of the constraint will be added later)
        starting_index = 0
        for _ in range(self.num_of_bins * len(self.categories)):
            row = [0] * self.num_of_bins * self.layout_slots * len(self.categories)
            row[starting_index:starting_index + self.layout_slots] = [1] * self.layout_slots
            self.alt_A.append(row.copy())
            starting_index += self.layout_slots

        # Slots' capacity constraints creation and insertion into matrix ads_A
        for i in range(self.layout_slots):
            row = [0] * self.num_of_bins * self.layout_slots * len(self.categories)
            target_index = i
            while target_index < len(row):
                row[target_index] = 1
                target_index += self.layout_slots

            self.alt_A.append(row.copy())

        # category diversity constraints (for each category we have num of bins * layout slots variables)
        starting_index = 0
        for _ in range(len(self.categories)):
            row = [0] * self.num_of_bins * self.layout_slots * len(self.categories)
            row[starting_index:starting_index + self.num_of_bins * self.layout_slots] = -1 * np.array(self.real_slot_promenances * \
                                                                                        self.num_of_bins)
            self.alt_A.append(row.copy())
            starting_index += self.num_of_bins * self.layout_slots

        # RESTRICTED ADS ILP:

        self.res_A = []
        self.res_B = [1] * self.ads_slots + [self.ads_slots] * 2 * len(self.categories) + [1 + self.M] * len(self.categories)
        self.res_C = []
        self.res_lambdas = []

        for _ in range(2 * len(self.categories)):
            self.res_lambdas += self.ads_real_slot_promenances

        for i in range(self.ads_slots):
            row = [0] * 2 * (len(self.categories) * self.ads_slots)
            target_index = i
            while target_index < len(row):
                row[target_index] = 1
                target_index += self.ads_slots

            self.res_A.append(row.copy())

        initial_index = 0
        for _ in range(2 * len(self.categories)):
            row = [0] * 2 * (len(self.categories) * self.ads_slots)
            row[initial_index:initial_index + self.ads_slots] = [1] * self.ads_slots
            self.res_A.append(row.copy())
            initial_index += self.ads_slots

        # Competitors exclusion constraints creation and insertion into matrix ads_A.
        initial_index = 0
        var_initial_index = self.ads_slots
        for _ in range(len(self.categories)):
            row = [0] * 2 * len(self.categories) * self.ads_slots
            row[initial_index:initial_index + 2 * self.ads_slots] = [1] * 2 * self.ads_slots
            row[var_initial_index:var_initial_index + self.ads_slots] = [1 + self.M] * self.ads_slots
            var_initial_index += 2 * self.ads_slots
            initial_index += 2 * self.ads_slots
            self.res_A.append(row.copy())


        self.full_A = []
        self.full_B = list(np.array(allocation_diversity_bounds) * -1) + [1] * self.layout_slots
        self.full_C = []
        self.full_lambdas = []
        self.full_variables = []

    def sample_quality(self, content, user, interest_decay=False):
        """
        Returns a sample from the proper weighted beta distribution
        :param content: The news for which we want a sample describing the probability the user clicks it
        :param user: The user itself, used to access its story with all the clicked/non-clicked news
        :param approach: Use position_based_model", ignore the rest for now
        :param interest_decay: Whether to consider if the user already clicked the news or whether it has already seen
        it etc. If so, returns a sample from the corresponding beta, otherwise froma fixed beta.
        :return: A sample from a proper beta distribution considering the category of the news passed as parameter
        """
        if isinstance(content, News):
            category = content.news_category
            if interest_decay:
                # Determines which beta to pull from:
                weighted_beta_matrix_posx, weighted_beta_matrix_posy = self.__compute_position_in_learning_matrix(user,
                                                                                                                  content)
                content.set_sampled_quality(value=self.weighted_betas_matrix[weighted_beta_matrix_posx]
                                                                            [weighted_beta_matrix_posy].sample(category=category))
            else:
                # Pulls from a fixed beta otherwise
                content.set_sampled_quality(value=self.weighted_betas_matrix[0][0].sample(category=category))

        elif isinstance(content, Ad):
            category = content.ad_category
            return self.ads_weighted_beta.sample(category=category)

        else:
            raise RuntimeError("Type of content not recognized. It must be either a News or an Ad content")

    def __compute_position_in_learning_matrix(self, user, news):
        """
        Observing the number of time the news has been allocated for user and the number of times the user already
        clicked the news, computes the position in the matrix of the corresponding weighted beta distribution
        :param user: The user itself
        :param news: The news itself
        :return: The coordinates of the corresponding beta in the weighted beta matrix
        """
        slot_promenance_cumsum = user.get_promenance_cumsum(news)
        total_num_of_clicks = user.get_amount_of_clicks(news)

        if slot_promenance_cumsum < self.news_column_pivots[-1]:
            k = 0
            while slot_promenance_cumsum >= self.news_column_pivots[k]:
                k += 1
            weighted_beta_matrix_posy = k
        else:
            weighted_beta_matrix_posy = len(self.news_column_pivots)

        if total_num_of_clicks < self.news_row_pivots[-1]:
            k = 0
            while total_num_of_clicks >= self.news_row_pivots[k]:
                k += 1
            weighted_beta_matrix_posx = k
        else:
            weighted_beta_matrix_posx = len(self.news_row_pivots)

        return weighted_beta_matrix_posx, weighted_beta_matrix_posy

    def ad_click(self, ad, slot_nr):
        """
        Communicate to the Agent the the ad "ad" has been clicked in slot "slot_nr"
        :param ad: An Ad object
        :param slot_nr: Integer between zero and len(ads_real_slot_prominences) - 1
        :return: None
        """
        self.ads_weighted_beta.click(ad, slot_nr)

    def news_click(self, content, user, slot_nr=[], interest_decay=False, fixed_learning_matrix_indexes=(-1, -1)):
        """
        Communicates (update the parameters) to the corresponding weighted beta distribution that a news has been
        clicked.
        :param content: A News Object. The clicked news
        :param user: An User object. The user that clicked
        :param slot_nr: Integer between zero and len(ads_real_slot_prominences) - 1. The slot in which the ad has been clicked
        :param interest_decay: Determines whether to communicate to the corresponding beta or to a fixed beta
        :param fixed_learning_matrix_indexes: used only with linear programming formulation "alt_LP" to handle
        sub-optimalities
        :return: Nothing
        """
        category_index = self.categories.index(content.news_category)
        if len(slot_nr) > 0:
            if interest_decay:
                # Computes the coordinates of the corresponding weighted beta dist. in the weighted beta matrix
                if fixed_learning_matrix_indexes[0] < 0:
                    weighted_beta_matrix_posx, weighted_beta_matrix_posy = self.__compute_position_in_learning_matrix(user,
                                                                                                                      content)
                else:
                    weighted_beta_matrix_posx = fixed_learning_matrix_indexes[0]
                    weighted_beta_matrix_posy = fixed_learning_matrix_indexes[1]
                self.weighted_betas_matrix[weighted_beta_matrix_posx][weighted_beta_matrix_posy].click(content, slot_nr[0])
                alloc_index = user.get_promenance_cumsum(content, get_only_index=True)
                click_index = user.get_amount_of_clicks(content, get_only_index=True)
                # Update with the values of the temporary variables
                user.last_news_in_allocation[alloc_index][1] = user.last_news_in_allocation[alloc_index][2]
                user.last_news_clicked[click_index][1] = user.last_news_clicked[click_index][2]
            else:
                # Otherwise update a fixed weighted beta matrix
                self.weighted_betas_matrix[0][0].click(content, slot_nr[0])

    def find_best_allocation(self, user, interest_decay=False, continuity_relaxation=True,
                             update_assignment_matrices=True):
        """
        For each news in the news pool set a news sample pulled from the corresponding beta distributions.
        Allocates the best news by adopting either the classic standard allocation approach (allocate best news starting
        from best slots) or the LP allocation approach, that makes use of a linear problem to solve the task. The
        linear problem approach takes into account also the variety of a page, making sure to give to each category
        a percentage of the total slot promenance of the page.
        :param user: The user to which we are presenting the page.
        :param interest_decay: Whether to pull from the corresponding weighted beta distribution or to pull from a fixed
        weighted beta distribution.
        :param continuity_relaxation: In case of linear problem approach, this variable discriminates between the
        continuity relaxation of the problem's variables, of to use binary variable (btw this option increases the
        complexity of the resolution, since it can be seen as an NP complete problem)
        :param update_assignment_matrices: Whether to update the corresponding weighted beta distribution with the
        performed allocations. Useful to be False only in case of testing the performances of a trained model.
        :return: A list of news corresponding to the allocation in the page. The order of the news in the list
        correspond to the order of the slots in which the news are allocated.
        """

        if len(self.news_pool) < self.layout_slots:
            raise RuntimeError("The news pool is empty or too few articles are present. Use the method fill_news_pool "
                               "before allocating pages.")

        result_news_allocation = [0] * self.layout_slots
        if not (self.allocation_approach == "alt_LP"):
            for news in self.news_pool:
                self.sample_quality(content=news, user=user, interest_decay=interest_decay)

        if self.allocation_approach == "standard":
            self.news_pool.sort(key=lambda x: x.sampled_quality, reverse=True)
            tmp_news_pool = self.news_pool.copy()
            slot_promenances = self.real_slot_promenances.copy()

            for i in range(len(slot_promenances)):
                target_slot_index = np.argmax(slot_promenances)
                assigning_news = tmp_news_pool.pop(0)
                result_news_allocation[int(target_slot_index)] = assigning_news
                slot_promenances[int(target_slot_index)] = -1

        elif self.allocation_approach == "LP":
            result_news_allocation = self.__solve_linear_problem(continuity_relaxation=continuity_relaxation)

        elif self.allocation_approach == "alt_LP":
            result_news_allocation = self.__solve_alternative_linear_problem(user=user)

        elif self.allocation_approach == "full":
            result_news_allocation = self.__solve_full_linear_problem()
        else:
            raise RuntimeError("Allocation approach not recognized. Use 'standard', 'LP', 'alt_LP' or 'full'")

        if update_assignment_matrices:
            # Update weighted betas parameters with the allocation results:
            for i in range(len(result_news_allocation)):
                if interest_decay:
                    weighted_beta_matrix_posx, weighted_beta_matrix_posy = self.__compute_position_in_learning_matrix(user,
                                                                                                                      result_news_allocation[i])
                    self.weighted_betas_matrix[weighted_beta_matrix_posx][weighted_beta_matrix_posy].allocation(result_news_allocation[i], i)
                    if (self.allocation_approach == "alt_LP") and \
                       (result_news_allocation.count(result_news_allocation[i]) > 1):
                        result_news_allocation[i].doubled_news_indexes = [weighted_beta_matrix_posx,
                                                                          weighted_beta_matrix_posy]

                    assigned_slot_promenance = self.real_slot_promenances[i]
                    # In the following, save into the user "cookie" that the current news has been allocated for him.
                    # If present, update the counter properly, if not a new entry is added.
                    index = user.get_promenance_cumsum(result_news_allocation[i], get_only_index=True)
                    if index == -1:
                        # Entry not found
                        length = len(user.last_news_in_allocation)
                        inserted = False
                        if length >= 2:
                            for k in range(length - 1):
                                if (result_news_allocation[i].news_id > user.last_news_in_allocation[k][0]) and (
                                        result_news_allocation[i].news_id < user.last_news_in_allocation[k + 1][0]):
                                    user.last_news_in_allocation.insert(k + 1, [result_news_allocation[i].news_id, 0, assigned_slot_promenance])
                                    inserted = True
                            if not inserted:
                                if result_news_allocation[i].news_id < user.last_news_in_allocation[0][0]:
                                    user.last_news_in_allocation.insert(0, [result_news_allocation[i].news_id, 0, assigned_slot_promenance])
                                    inserted = True
                        elif length == 1:
                            if user.last_news_in_allocation[0][0] > result_news_allocation[i].news_id:
                                user.last_news_in_allocation.insert(0, [result_news_allocation[i].news_id, 0, assigned_slot_promenance])
                                inserted = True

                        if not inserted:
                            user.last_news_in_allocation.append([result_news_allocation[i].news_id, 0, assigned_slot_promenance])
                    else:
                        # Entry found
                        user.last_news_in_allocation[index][2] += assigned_slot_promenance
                else:
                    self.weighted_betas_matrix[0][0].allocation(result_news_allocation[i], i)

        return result_news_allocation

    def fill_news_pool(self, news_list, append=True):
        """
        Fills the news pool with a list of news. Always to be done before starting any process with this learner.
        The news objects must have categories handles by the Agent.
        :param news_list: 1D list of News objects. The list of news itself-
        :param append: If true append each element of the list, otherwise copies the entire list
        :return: Nothing.
        """
        for news in news_list:
            if not isinstance(news, News):
                raise RuntimeError("Only News objects can be stored with the method fill_news_pool")
            if news.news_category not in self.categories:
                raise RuntimeError("Only News objects with category stored in the Agent categories can be stored"
                                   "with the method fill_news_pool. An News with category " + news.news_category +
                                   " is trying to be stored, but only categories " + str(self.categories) + " can"
                                                                                                            "be handled by the Agent.")
        if append:
            for news in news_list:
                self.news_pool.append(news)
        else:
            self.news_pool = news_list

        if self.allocation_approach == "full":
            for news in self.news_pool:
                index = self.categories.index(news.news_category)
                self.news_per_category[index] += 1
            self.news_pool.sort(key=lambda x: x.news_category, reverse=False)

        num_of_active_news = sum(self.news_per_category)
        self.full_B += [1] * num_of_active_news

        for _ in range(num_of_active_news):
            self.full_lambdas += list(np.array(self.real_slot_promenances) * -1)

        initial_index = 0
        for cat_index in range(len(self.categories)):
            row = [0] * (num_of_active_news * self.layout_slots)
            row_slot_promenances = []
            for _ in range(self.news_per_category[cat_index]):
                row_slot_promenances += list(np.array(self.real_slot_promenances) * -1)
            row[initial_index:initial_index + self.news_per_category[cat_index] * self.layout_slots] = row_slot_promenances
            self.full_A.append(row.copy())
            initial_index += self.news_per_category[cat_index] * self.layout_slots

        for i in range(self.layout_slots):
            row = [0] * (num_of_active_news * self.layout_slots)
            target_index = i
            while target_index < len(row):
                row[target_index] = 1
                target_index += self.layout_slots

            self.full_A.append(row.copy())

        initial_index = 0
        for _ in range(num_of_active_news):
            row = [0] * (num_of_active_news * self.layout_slots)
            row[initial_index:initial_index + self.layout_slots] = [1] * self.layout_slots
            self.full_A.append(row.copy())
            initial_index += self.layout_slots

        for cat in range(len(self.categories)):
            for num in range(self.news_per_category[cat]):
                for s in range(self.layout_slots):
                    self.full_variables.append(LpVariable(name=str(cat) + "_" + str(num) + "_" + str(s), lowBound=0, upBound=1, cat="Continuous"))

    def fill_ads_pool(self, ads_list, append=True):
        """
            Fills the ads pool with a list of ads. Always to be done before starting any advertising process with this learner.
            The news objects must have categories handles by the Agent.
            :param ads_list: 1D list of Ad objects. The list of ads itself-
            :param append: If true append each element of the list, otherwise copies the entire list
            :return: Nothing.
        """
        for ad in ads_list:
            if not isinstance(ad, Ad):
                raise RuntimeError("Only Ad objects can be stored with the method fill_ads_pool")
            if ad.ad_category not in self.categories:
                raise RuntimeError("Only News objects with category stored in the Agent categories can be stored"
                                   "with the method fill_news_pool. An News with category " + ad.ad_category +
                                   " is trying to be stored, but only categories " + str(self.categories) + " can"
                                                                                                            "be handled by the Agent.")
        if append:
            for ad in ads_list:
                self.ads_pool.append(ad)
        else:
            self.ads_pool = ads_list

        for ad in ads_list:
            cat_index = self.categories.index(ad.ad_category)
            if ad.exclude_competitors:
                ex_index = 1
            else:
                ex_index = 0
            self.ads_per_category[cat_index][ex_index].append(ad)

    def refresh_ads_buffer(self, ads_list):
        for ad in ads_list:
            cat_index = self.categories.index(ad.ad_category)
            if ad.exclude_competitors:
                ex_index = 1
            else:
                ex_index = 0
            self.ads_per_category[cat_index][ex_index].append(ad)

    def find_ads_best_allocation(self, news_allocation):

        if len(self.ads_pool) < self.ads_slots:
            raise RuntimeError("The ads pool is empty or too few articles are present. Use the method fill_ads_pool "
                               "before advertising pages.")

        if self.ads_allocation_technique == "LP":
            ads_allocation = self.__solve_ads_integer_linear_problem(news_allocation=news_allocation)
        elif self.ads_allocation_technique == "res_LP":
            ads_allocation = self.__solve_ads_restricted_linear_problem(news_allocation=news_allocation)
        else:
            raise RuntimeError("Ads allocation method not recognized. Use 'LP' or 'resLP'.")

        final_ads_allocation = []

        if self.ads_allocation_approach == "pdda":
            for ad in ads_allocation:
                if not ad.is_buyer():
                    outcome = np.random.binomial(1, 0.5)
                    if outcome == 1:
                        final_ads_allocation.append(ad)
                        self.remove_ad_from_pool([ad])
                    else:
                        ad.set_as_buyer()
                else:
                    final_ads_allocation.append(ad)
                    self.remove_ad_from_pool([ad])
        elif self.ads_allocation_approach == "greedy":
            self.remove_ad_from_pool(ads_allocation)
            final_ads_allocation = ads_allocation
        elif self.ads_allocation_approach == "wpdda":
            for ad in ads_allocation:
                if not ad.is_buyer():
                    outcome = np.random.binomial(1, ad.sampled_quality)
                    if outcome == 1:
                        final_ads_allocation.append(ad)
                        self.remove_ad_from_pool([ad])
                    else:
                        ad.set_as_buyer()
                else:
                    final_ads_allocation.append(ad)
                    self.remove_ad_from_pool([ad])
        else:
            raise RuntimeError("Ads allocation approach not recognized. Choose among 'pdda', 'wpdda', 'greedy'")

        for i in range(len(final_ads_allocation)):
            self.ads_weighted_beta.allocation(final_ads_allocation[i], slot_index=i)

        self.total_ads_clicks_and_displays.append([0, len(final_ads_allocation)])

        return final_ads_allocation

    def user_arrival(self, user, interest_decay=False):
        """
        This method defines the procedure to be adopted when a simulated user interacts with the site (
        and then with the learner).
        First finds the best page allocation for that user, by using a fixed or corresponding beta distributions.
        Collect then the user interactions with the page and update the corresponding beta distributions.
        Collect also the average quality of the page depending on the user tastes and the number of received clicks.
        :param user: A User object. The user itself
        :param interest_decay: Whether to pull from the corresponding weighted beta distribution or to pull from a fixed
        weighted beta distribution.
        :return: Nothing.
        """

        if not isinstance(user, SyntheticUser):
            raise RuntimeError("Only user objects can be passed as user parameter")
        t1 = time.time()
        allocation = self.find_best_allocation(user=user, interest_decay=interest_decay)
        t2 = time.time()
        t3 = 0
        t4 = 0
        arm_rewards = []
        page_clicks = 0

        for i in range(len(allocation)):
            outcome = np.random.binomial(1, self.real_slot_promenances[i])
            arm_rewards.append(user.get_reward(allocation[i]))
            if outcome == 1:
                clicked = user.click_news(allocation[i], interest_decay=interest_decay)
                if clicked == 1:

                    if allocation.count(allocation[i]) == 1:
                        self.news_click(content=allocation[i], slot_nr=[i], interest_decay=interest_decay, user=user)
                    else:
                        self.news_click(content=allocation[i], slot_nr=[i], interest_decay=interest_decay, user=user,
                                        fixed_learning_matrix_indexes=(allocation[i].doubled_news_indexes[0],
                                                                       allocation[i].doubled_news_indexes[1]))
                    page_clicks += 1
                else:
                    if interest_decay:
                        index = user.get_promenance_cumsum(allocation[i], get_only_index=True)
                        user.last_news_in_allocation[index][1] = user.last_news_in_allocation[index][2]
            elif interest_decay:
                index = user.get_promenance_cumsum(allocation[i], get_only_index=True)
                user.last_news_in_allocation[index][1] = user.last_news_in_allocation[index][2]

        if self.ads_allocation:
            t3 = time.time()
            ads_allocation = self.find_ads_best_allocation(news_allocation=allocation)
            t4 = time.time()
            for i in range(len(ads_allocation)):
                outcome = np.random.binomial(1, self.ads_real_slot_promenances[i])
                if outcome == 1:
                    clicked = user.click_ad(ads_allocation[i])
                    if clicked == 1:
                        self.ad_click(ad=ads_allocation[i],
                                      slot_nr=i)
                        self.total_ads_clicks_and_displays[-1][0] += 1

        self.news_times.append(t2 - t1)
        self.ads_times.append(t4 - t3)
        self.multiple_arms_avg_reward.append(np.mean(arm_rewards))
        self.click_per_page.append(page_clicks)

    def save_weighted_beta_matrices(self, desinence):
        """
        Saves in .txt files the content of all the weighted beta distribution present in the weighted beta matrix.
        Add a specific desinence to the file name in order to distinguish different learning matrices from different
        learners
        :param desinence: The desinence itself
        :return: Nothing
        """
        for i in range(len(self.weighted_betas_matrix)):
            for j in range(len(self.weighted_betas_matrix[i])):
                file = open("Weighted_Beta_" + str(i) + "_" + str(j) + "_reward_" + desinence + ".txt", "w")
                for reward_row in self.weighted_betas_matrix[i][j].category_per_slot_reward_count:
                    file.write(str(reward_row[0]))
                    for k in range(1, len(reward_row)):
                        file.write("," + str(reward_row[k]))
                    file.write("\n")
                file.close()
                file = open("Weighted_Beta_" + str(i) + "_" + str(j) + "_assignment_" + desinence + ".txt", "w")
                for assignment_row in self.weighted_betas_matrix[i][j].category_per_slot_assignment_count:
                    file.write(str(assignment_row[0]))
                    for k in range(1, len(assignment_row)):
                        file.write("," + str(assignment_row[k]))
                    file.write("\n")
                file.close()

    def save_ads_weighted_beta(self, desinence):
        """
            Saves in .txt files the content of the weighted beta distribution for the advertising.
            Add a specific desinence to the file name in order to distinguish different learning matrices from different
            learners
            :param desinence: The desinence itself
            :return: Nothing
        """
        file = open("Ads_Weighted_Beta_reward_" + desinence + ".txt", "w")
        for reward_row in self.ads_weighted_beta.category_per_slot_reward_count:
            file.write(str(reward_row[0]))
            for k in range(1, len(reward_row)):
                file.write("," + str(reward_row[k]))
            file.write("\n")
        file.close()
        file = open("Ads_Weighted_Beta_assignment_" + desinence + ".txt", "w")
        for assignment_row in self.ads_weighted_beta.category_per_slot_assignment_count:
            file.write(str(assignment_row[0]))
            for k in range(1, len(assignment_row)):
                file.write("," + str(assignment_row[k]))
            file.write("\n")

    def insert_into_news_pool(self, news):
        """
        Add a news into the news pool.
        :param news: The news itself
        :return: Nothing
        """
        self.news_pool.append(news)

    def read_weighted_beta_matrix_from_file(self, indexes, desinences, folder="Trained_betas_matrices/"):
        """
        Read the parameters of some weighted beta distribution from file. In particular, the wheighted betas that are
        going to be read are the ones in the matrix specified by the indexes touples present in the parameter "indexes".
        :param indexes: List of touples containing the indexes of the weighted beta distribution to be read from file
        :param desinences: A specific desinence of the file we are reading from
        :param folder: An eventual folder in which the files are contained. Specify "/" at the end of the folder name.
        :return: Nothing.
        """
        for i in range(len(indexes)):
            matrix = []
            file = open(folder + "Weighted_Beta_" + str(indexes[i][0]) + "_" + str(indexes[i][1]) + "_assignment_" +
                        str(desinences[i]) + ".txt", 'r')
            lines = file.read().splitlines()
            for line in lines:
                line_splitted = line.split(",")
                matrix.append(list(map(float, line_splitted)))
            self.weighted_betas_matrix[indexes[i][0]][indexes[i][1]].category_per_slot_assignment_count = matrix.copy()

            matrix.clear()
            file = open(folder + "Weighted_Beta_" + str(indexes[i][0]) + "_" + str(indexes[i][1]) + "_reward_" + str(
                desinences[i]) + ".txt", 'r')
            lines = file.read().splitlines()
            for line in lines:
                line_splitted = line.split(",")
                matrix.append(list(map(float, line_splitted)))
            self.weighted_betas_matrix[indexes[i][0]][indexes[i][1]].category_per_slot_reward_count = matrix.copy()

    def read_ads_weighted_beta_matrix_from_file(self, desinence, folder=""):
        """
        Read the ads w. beta matrix from a file wih a given desinence. The folder in which the file is saved can be specified
        :param desinence: str desinence
        :param folder: file path
        :return: nothing
        """
        matrix = []
        file = open(folder + "Ads_Weighted_Beta_assignment_" +
                    str(desinence) + ".txt", 'r')
        lines = file.read().splitlines()
        for line in lines:
            line_splitted = line.split(",")
            matrix.append(list(map(float, line_splitted)))
        self.ads_weighted_beta.category_per_slot_assignment_count = matrix.copy()

        matrix.clear()
        file = open(folder + "Ads_Weighted_Beta_reward_" + str(
            desinence) + ".txt", 'r')
        lines = file.read().splitlines()
        for line in lines:
            line_splitted = line.split(",")
            matrix.append(list(map(float, line_splitted)))
        self.ads_weighted_beta.category_per_slot_reward_count = matrix.copy()

    def remove_news_from_pool(self, news_list):
        """
        Remove all the news present in the news_list from the news pool.
        :param news_list: 1D List of News objects. The news list itself.
        :return: Nothing.
        """
        for i in range(-len(self.news_pool), 0):
            if self.news_pool[i] in news_list:
                self.news_pool.__delitem__(i)

    def remove_ad_from_pool(self, ads_list):
        """
        Remove a set of ads from the ads pool
        :param ads_list: 1D list of Ad objects
        :return: Nothing
        """
        for i in range(-len(self.ads_pool), 0):
            if self.ads_pool[i] in ads_list:
                self.ads_pool.__delitem__(i)

        if self.ads_allocation_technique == "resLP":
            for ad in ads_list:
                cat_index = self.categories.index(ad.ad_category)
                if ad.exclude_competitors:
                    ex_index = 1
                else:
                    ex_index = 0
                self.ads_per_category[cat_index][ex_index].remove(ad)

    def measure_allocation_diversity_bounds_errors(self, slots_assegnation_probabilities, LP_news_pool, iter=5000):
        """
        This method only checks and collect data about how good are the three possible de-randomization techniques in
        respecting the diversity bounds formulated in the LP (with continuity relaxation). The data are collected by
        running "iter" number of derandomizations and are saved in the class attributes: "rand_1_errors",
        "rand_2_errors" and "rand_3_errors". The single error per derandomization is quantified as the mximum
        percentage of displacement bewteen the required and presented promenance per category.
        :param slots_assegnation_probabilities: The randomized solution of a LP.
        :param LP_news_pool: The restricted news pool used by the LP.
        :param iter: Number of the derandomization performed for each technique
        :return: Nothing.
        """
        for tech in ["rand_1", "rand_2", "rand_3"]:
            max_errors_per_iter = []
            for k in range(iter):
                tmp_slots_assegnation_probabilities = []
                for elem in slots_assegnation_probabilities:
                    tmp_slots_assegnation_probabilities.append(elem.copy())
                constraints_error = [0] * len(self.categories)
                promenance_per_category = [0] * len(self.categories)
                result = self.__de_randomize_LP(LP_news_pool, tmp_slots_assegnation_probabilities, tech)
                for i in range(len(result)):
                    category_index = self.categories.index(result[i].news_category)
                    promenance_per_category[category_index] += self.real_slot_promenances[i]

                for i in range(len(promenance_per_category)):
                    if promenance_per_category[i] < self.B[i] * -1:
                        constraints_error[i] += (self.B[i] * -1 - promenance_per_category[i]) / (self.B[i] * -1)

                max_errors_per_iter.append(np.mean(constraints_error))
            if tech == "rand_1":
                self.rand_1_errors += max_errors_per_iter
            elif tech == "rand_2":
                self.rand_2_errors += max_errors_per_iter
            else:
                self.rand_3_errors += max_errors_per_iter

    def __solve_linear_problem(self, continuity_relaxation=True):
        """
        Solve a linear problem to find the best allocation for the current page.
        First selects a subset of "num_of_slots" news for each category.
        If there are not at least "num_of_slots" news for each category random news from the news pool will be chosen.
        this will lead the solution to be significantly worse. In real scenarios this case will never happen.
        Using the selected news solves the linear problem either with continuity relaxation of the variable or without
        it.
        :param continuity_relaxation: Whether to use an LP approach or an ILP approach.
        :return: 1D List of News. A list of news corresponding to the allocation in the page. The order of the news in the list
        correspond to the order of the slots in which the news are allocated.
        """
        result = [0] * self.layout_slots
        self.news_pool.sort(key=lambda x: (x.news_category, x.sampled_quality), reverse=True)
        LP_news_pool = []
        done_for_category = False
        category_count = 0
        prev_category = self.news_pool[0].news_category
        # First build a subset of news to easily handle the LP resolution
        for news in self.news_pool:
            if prev_category != news.news_category:
                if category_count < self.layout_slots:
                    raise RuntimeWarning("Not enough news per category found. There should be at least " +
                                         str(self.layout_slots) + " news with category = " + prev_category + ", but "
                                         "only " + str(category_count) + "are present. The allocation maybe "
                                                                         "sub-optimal.")
                category_count = 0
                done_for_category = False
                prev_category = news.news_category
            if not done_for_category:
                LP_news_pool.append(news)
                category_count += 1
            if category_count == self.layout_slots:
                done_for_category = True

        # If not all the required news are present, add some other news at random.
        while len(LP_news_pool) < len(self.categories) * self.layout_slots:
            random_news = np.random.choice(self.news_pool)
            if random_news not in LP_news_pool:
                LP_news_pool.append(random_news)

        LP_news_pool.sort(key=lambda x: x.news_category, reverse=False)
        thetas = []
        # Compute the vector of coefficients for the LP objective function
        for news in LP_news_pool:
            thetas += [news.sampled_quality] * self.layout_slots
        self.C = list(np.array(thetas) * np.array(self.lambdas))

        # Then solve an LP or an ILP
        if continuity_relaxation:
            linear_problem = opt.linprog(A_ub=self.A, b_ub=self.B, c=self.C)
            slots_assegnation_probabilities = []
            slot_counter = 0
            tmp_slot_probabilities = []
            while slot_counter < self.layout_slots:
                i = slot_counter
                while i < len(linear_problem.x):
                    tmp_slot_probabilities.append(np.abs(linear_problem.x[i]))
                    i += self.layout_slots
                slots_assegnation_probabilities.append(tmp_slot_probabilities.copy())
                tmp_slot_probabilities.clear()
                slot_counter += 1

            self.measure_allocation_diversity_bounds_errors(slots_assegnation_probabilities, LP_news_pool, iter=10)

            result = self.__de_randomize_LP(LP_news_pool, slots_assegnation_probabilities, self.lp_rand_tech)

        else:
            # INITIALIZES AN INTEGER LINEAR PROBLEM
            ILP = LpProblem("News_ILP", LpMaximize)
            ILP_variables = []

            for cat in range(len(self.categories)):
                for j in range(self.layout_slots):
                    for s in range(self.layout_slots):
                        ILP_variables.append(LpVariable(name=str(cat) + "_" + str(j) + "_" + str(s), lowBound=0, upBound=1, cat="Binary"))

            # Objective function addition to the problem
            C = list(np.array(self.C) * -1)
            ILP += lpSum([C[i] * ILP_variables[i] for i in range(len(self.C))])

            # Category constraints addition to the problem
            for i in range(len(self.categories)):
                ILP += lpSum([self.A[i][j] * ILP_variables[j] for j in range(len(self.C))]) <= self.B[i]

            # Slots capacity constraints addition to the problem
            for i in range(len(self.categories), len(self.categories) + self.layout_slots):
                ILP += lpSum([self.A[i][j] * ILP_variables[j] for j in range(len(self.C))]) <= self.B[i]

            # News capacity constraints addition to the problem
            for i in range(len(self.categories) + self.layout_slots, len(self.categories) + self.layout_slots + len(self.categories) * self.layout_slots):
                ILP += lpSum([self.A[i][j] * ILP_variables[j] for j in range(len(self.C))]) <= self.B[i]

            ILP.solve()

            # FOR EACH SLOT, ISOLATES THE CORRESPONDING VARIABLES
            slots_assegnation_probabilities = []
            slot_counter = 0
            tmp_slot_probabilities = []
            while slot_counter < self.layout_slots:
                i = slot_counter
                while i < len(ILP.variables()):
                    tmp_slot_probabilities.append(ILP.variables().__getitem__(i))
                    i += self.layout_slots
                slots_assegnation_probabilities.append(tmp_slot_probabilities.copy())
                tmp_slot_probabilities.clear()
                slot_counter += 1

            # TAKES THE VARIABLES WHICH VALUE IS 1, THEN ALLOCATES THE CORRESPONDING NEWS IN THE RESULT PAGE
            for i in range(len(result)):
                for probabilities in slots_assegnation_probabilities[i]:
                    if probabilities.varValue > 0:
                        var_name = probabilities.name
                        break
                indexes = var_name.split("_")
                category_index = int(indexes[0])
                news_number = int(indexes[1])
                news_index = category_index * self.layout_slots + news_number
                result[i] = LP_news_pool[news_index]

        return result

    def __solve_full_linear_problem(self):
        """
        Solve the linear problem associated to news allocation without any optimization.
        :return: 1D List of News. A list of news corresponding to the allocation in the page. The order of the news in the list
        correspond to the order of the slots in which the news are allocated.
        """
        samples = []

        for news in self.news_pool:
            samples += [news.sampled_quality] * self.layout_slots

        self.full_C = np.array(samples) * self.full_lambdas

        linear_problem = opt.linprog(A_ub=self.full_A, b_ub=self.full_B, c=self.full_C)
        slots_assegnation_probabilities = []
        slot_counter = 0
        tmp_slot_probabilities = []
        while slot_counter < self.layout_slots:
            i = slot_counter
            while i < len(linear_problem.x):
                tmp_slot_probabilities.append(np.abs(linear_problem.x[i]))
                i += self.layout_slots
            slots_assegnation_probabilities.append(tmp_slot_probabilities.copy())
            tmp_slot_probabilities.clear()
            slot_counter += 1

        result = self.__de_randomize_LP(self.news_pool, slots_assegnation_probabilities, self.lp_rand_tech)

        return result

    def __solve_alternative_linear_problem(self, user):
        """
        Solve the alternative LP for articles.
        :param user: An user object
        :return: 1D List of News. A list of news corresponding to the allocation in the page. The order of the news in the list
        correspond to the order of the slots in which the news are allocated.
        """
        result = [0] * self.layout_slots
        de_rand_approach = "greedy"
        bins_per_category = []
        bins_cardinality = []
        for _ in range(len(self.categories)):
            bins_per_category.append([])
            bins_cardinality.append([])

        for cat in range(len(self.categories)):
            for _ in range(len(self.news_row_pivots) + 1):
                bins_per_category[cat].append([])
                bins_cardinality[cat].append([])
                for _ in range(len(self.news_column_pivots) + 1):
                    bins_per_category[cat][-1].append([])
                    bins_cardinality[cat][-1].append(0)

        for news in self.news_pool:
            category_index = self.categories.index(news.news_category)
            x, y = self.__compute_position_in_learning_matrix(user=user, news=news)
            bins_per_category[category_index][x][y].append(news)
            bins_cardinality[category_index][x][y] += 1

        index = 0
        bin_samples = []
        for cat in range(len(self.categories)):
            for x in range(len(self.news_row_pivots) + 1):
                for y in range(len(self.news_column_pivots) + 1):
                    if (y == 0) and (x != 0):
                        continue
                    self.alt_B[index] = min(bins_cardinality[cat][x][y], self.layout_slots)
                    index += 1
                    try:
                        selected_news = np.random.choice(bins_per_category[cat][x][y])
                        self.sample_quality(selected_news, user, interest_decay=True)
                        bin_samples += [selected_news.sampled_quality] * self.layout_slots
                    except ValueError:
                        bin_samples += [0] * self.layout_slots

        self.alt_C = np.array(list(np.array(self.alt_lambdas) * bin_samples)) * -1
        linear_problem = opt.linprog(A_ub=self.alt_A, b_ub=self.alt_B, c=self.alt_C)

        # FOR EACH SLOT, ISOLATES THE CORRESPONDING VARIABLES
        slots_assegnation_probabilities = []
        slot_counter = 0
        tmp_slot_probabilities = []
        while slot_counter < self.layout_slots:
            i = slot_counter
            while i < len(linear_problem.x):
                tmp_slot_probabilities.append(np.abs(linear_problem.x[i]))
                i += self.layout_slots
            slots_assegnation_probabilities.append(tmp_slot_probabilities.copy())
            tmp_slot_probabilities.clear()
            slot_counter += 1

        slot_promenances = self.real_slot_promenances.copy()
        slot_promenances_norm = np.array(slot_promenances) / sum(slot_promenances)
        slots_nr = [s for s in range(0, self.layout_slots)]
        for i in range(self.layout_slots):
            if de_rand_approach == "ordered":
                k = i
            elif (de_rand_approach == "greedy") or (de_rand_approach == "greedy_max"):
                k = np.argmax(slot_promenances)
                slot_promenances[k] = 0
            elif de_rand_approach == "randomized":
                k = np.random.choice(slots_nr, p=slot_promenances_norm)
                slot_promenances[k] = 0
            else:
                raise RuntimeError("De_randomization approach not recognized. Try either 'ordered', 'greedy', "
                                   "'randomized' or 'greedy_max'.")

            target_slot_probabilities = [x for x in slots_assegnation_probabilities[k]]
            target_slot_probabilities_norm = np.array(target_slot_probabilities) / sum(target_slot_probabilities)
            if de_rand_approach == "greedy_max":
                assigning_bin_index = np.argmax(target_slot_probabilities)
                cat_index = int(assigning_bin_index / self.num_of_bins)
                x = self.bins_for_position[int(assigning_bin_index)][0]
                y = self.bins_for_position[int(assigning_bin_index)][1]

            else:
                assigning_bin = np.random.choice([x for x in range(len(slots_assegnation_probabilities[k]))], p=target_slot_probabilities_norm)
                cat_index = int(assigning_bin / self.num_of_bins)
                x = self.bins_for_position[int(assigning_bin)][0]
                y = self.bins_for_position[int(assigning_bin)][1]

            result[k] = np.random.choice(bins_per_category[cat_index][x][y])

        return result

    def __solve_ads_integer_linear_problem(self, news_allocation):
        """
        Solve the advertising integer LP.
        :param news_allocation: 1D list of a page allocation returned by the find_best_allocation method
        :return: 1D list of Ads objects. It corresponds toan Ads allocation on the page.
        """
        result = [0] * self.ads_slots
        category_percentage_in_allocation = [0] * len(self.categories)
        for i in range(len(news_allocation)):
            category_index = self.categories.index(news_allocation[i].news_category)
            category_percentage_in_allocation[category_index] += self.real_slot_promenances[i]
        category_percentage_in_allocation = list(np.array(category_percentage_in_allocation) / sum(self.real_slot_promenances))

        for ad in self.ads_pool:
            ad.set_sampled_quality(value=self.sample_quality(content=ad, user=None))
        if self.maximize_for_bids:
            self.ads_pool.sort(key=lambda x: (x.ad_category, x.sampled_quality * x.bid), reverse=True)
        else:
            self.ads_pool.sort(key=lambda x: (x.ad_category, x.sampled_quality), reverse=True)
        ads_ILP_news_pool = []
        done_for_category = False
        category_count = 0
        prev_category = self.ads_pool[0].ad_category
        # First build a subset of ads to easily handle the ILP resolution
        for ad in self.ads_pool:
            if prev_category != ad.ad_category:
                if category_count < self.ads_slots:
                    print("Not enough news per category found. There should be at least " +
                                         str(self.ads_slots) + " news with category = " + prev_category + ", but "
                                         "only " + str(category_count) + "are present. The allocation maybe "
                                         "sub-optimal.")
                category_count = 0
                done_for_category = False
                prev_category = ad.ad_category
            if not done_for_category:
                ads_ILP_news_pool.append(ad)
                category_count += 1
            if category_count == self.ads_slots:
                done_for_category = True

        ads_ILP_news_pool.sort(key=lambda x: x.ad_category, reverse=False)
        thetas = []
        percentages = []
        # Compute the vector of coefficients for the LP objective function
        competitors_constraints_starting_index = self.ads_slots + len(self.categories) * self.ads_slots
        for i in range(len(ads_ILP_news_pool)):
            ad_category_index = self.categories.index(ads_ILP_news_pool[i].ad_category)
            ad_category_percentage = category_percentage_in_allocation[ad_category_index]
            if self.maximize_for_bids:
                thetas += [ads_ILP_news_pool[i].sampled_quality * ads_ILP_news_pool[i].bid] * self.ads_slots
            else:
                thetas += [ads_ILP_news_pool[i].sampled_quality] * self.ads_slots
            percentages += [ad_category_percentage] * self.ads_slots
            self.ads_B[competitors_constraints_starting_index + i] = self.M * (2 - ads_ILP_news_pool[i].exclude_competitors)

        self.ads_C = list(np.array(thetas) * np.array(self.ads_lambdas) * np.array(percentages))

        # INITIALIZES AN INTEGER LINEAR PROBLEM
        ILP = LpProblem("Ads_ILP", LpMaximize)
        ILP_variables = []

        for cat in range(len(self.categories)):
            for j in range(self.ads_slots):
                for s in range(self.ads_slots):
                    ILP_variables.append(
                        LpVariable(name=str(cat) + "_" + str(j) + "_" + str(s), lowBound=0, upBound=1, cat="Binary"))

        # Objective function addition to the problem
        ILP += lpSum([self.ads_C[i] * ILP_variables[i] for i in range(len(self.ads_C))])

        # Slots capacity constraints addition to the problem
        for i in range(self.ads_slots):
            ILP += lpSum([self.ads_A[i][j] * ILP_variables[j] for j in range(len(self.ads_C))]) <= self.ads_B[i]

        # Ads capacity constraints addition to the problem
        for i in range(self.ads_slots, self.ads_slots + len(self.categories) * self.ads_slots):
            ILP += lpSum([self.ads_A[i][j] * ILP_variables[j] for j in range(len(self.ads_C))]) <= self.ads_B[i]

        # Competitors exclusion constraints addition to the problem
        for i in range(self.ads_slots + len(self.categories) * self.ads_slots,
                       self.ads_slots + len(self.categories) * self.ads_slots +
                       len(self.categories) * self.ads_slots):
            ILP += lpSum([self.ads_A[i][j] * ILP_variables[j] for j in range(len(self.ads_C))]) <= self.ads_B[i]

        ILP.solve()

        # FOR EACH SLOT, ISOLATES THE CORRESPONDING VARIABLES
        slots_assegnation_probabilities = []
        slot_counter = 0
        tmp_slot_probabilities = []
        while slot_counter < self.ads_slots:
            i = slot_counter
            while i < len(ILP.variables()):
                tmp_slot_probabilities.append(ILP.variables().__getitem__(i))
                i += self.ads_slots
            slots_assegnation_probabilities.append(tmp_slot_probabilities.copy())
            tmp_slot_probabilities.clear()
            slot_counter += 1

        # TAKES THE VARIABLES WHICH VALUE IS 1, THEN ALLOCATES THE CORRESPONDING AD IN THE RESULT PAGE
        for i in range(len(result)):
            for probabilities in slots_assegnation_probabilities[i]:
                if probabilities.varValue > 0:
                    var_name = probabilities.name
                    break
            indexes = var_name.split("_")
            category_index = int(indexes[0])
            ad_number = int(indexes[1])
            ad_index = category_index * self.ads_slots + ad_number
            result[i] = ads_ILP_news_pool[ad_index]

        if len(self.other_classes_learners) > 0:
            for elem in result:
                other_learners_samples = []
                for learner in self.other_classes_learners:
                    other_learners_samples.append(learner.sample_quality(elem, user=None))
                elem.sampled_quality = elem.sampled_quality / (elem.sampled_quality + sum(other_learners_samples))

        return result

    def __solve_ads_restricted_linear_problem(self, news_allocation):
        """
            Solve the advertising integer LP. this is an optimization of the classic ILP, very recommended, very fast.
            :param news_allocation: 1D list of a page allocation returned by the find_best_allocation method
            :return: 1D list of Ads objects. It corresponds toan Ads allocation on the page.
        """
        result = [0] * self.ads_slots
        tmp_category_percentage_in_allocation = [0] * len(self.categories)
        for i in range(len(news_allocation)):
            category_index = self.categories.index(news_allocation[i].news_category)
            tmp_category_percentage_in_allocation[category_index] += self.real_slot_promenances[i]
        tmp_category_percentage_in_allocation = list(np.array(tmp_category_percentage_in_allocation) / sum(self.real_slot_promenances))
        category_percentage_in_allocation = []
        for elem in tmp_category_percentage_in_allocation:
            category_percentage_in_allocation.append(elem)
            category_percentage_in_allocation.append(elem)

        category_samples = []

        for cat in self.ads_per_category:
            for mutual_ex_set in cat:
                try:
                    random_ad = np.random.choice(mutual_ex_set)
                    category_samples.append(self.sample_quality(random_ad, user=None))
                except ValueError:
                    category_samples.append(1)

        C = []
        for i in range(2 * len(self.categories)):
            # substitutes back: category_percentage_in_allocation[i]
            C += [category_samples[i] * 1] * self.ads_slots

        self.res_C = list(np.array(C) * self.res_lambdas)

        LP = LpProblem("lp_restricted_ads", LpMaximize)
        LP_variables = []

        for i in range(len(self.categories)):
            for j in [0, 1]:
                for k in range(self.ads_slots):
                    LP_variables.append(LpVariable(str(i) + "_" + str(j) + "_" + str(k), lowBound=0, upBound=self.ads_slots, cat=LpInteger))

        LP += lpSum([LP_variables[i] * self.res_C[i] for i in range(len(self.res_C))])
        for i in range(self.ads_slots):
            LP += lpSum([LP_variables[j] * self.res_A[i][j] for j in range(len(self.res_C))]) <= self.res_B[i]

        for i in range(self.ads_slots, self.ads_slots + 2 * len(self.categories)):
            x = int((i - self.ads_slots) / 2)
            y = int((i - self.ads_slots) % 2)
            LP += lpSum([LP_variables[j] * self.res_A[i][j] for j in range(len(self.res_C))]) <= min(self.res_B[i], len(self.ads_per_category[x][y]))

        for i in range(self.ads_slots + 2 * len(self.categories), self.ads_slots + 2 * len(self.categories) + len(self.categories)):
            LP += lpSum([LP_variables[j] * self.res_A[i][j] for j in range(len(self.res_C))]) <= self.res_B[i]

        LP.solve()

        # FOR EACH SLOT, ISOLATES THE CORRESPONDING VARIABLES
        slots_assegnation_probabilities = []
        slot_counter = 0
        tmp_slot_probabilities = []
        while slot_counter < self.ads_slots:
            i = slot_counter
            while i < len(LP.variables()):
                tmp_slot_probabilities.append(LP.variables().__getitem__(i))
                i += self.ads_slots
            slots_assegnation_probabilities.append(tmp_slot_probabilities.copy())
            tmp_slot_probabilities.clear()
            slot_counter += 1

        # TAKES THE VARIABLES WHICH VALUE IS >0, THEN ALLOCATES THE CORRESPONDING AD IN THE RESULT PAGE
        for i in range(len(result)):
            for probabilities in slots_assegnation_probabilities[i]:
                try:
                    if probabilities.varValue > 0:
                        var_name = probabilities.name
                        break
                except TypeError:
                    continue

            try:
                indexes = var_name.split("_")
            except UnboundLocalError:
                print("Not enough ads to display in the pool.")
                exit(-1)

            try:
                category_index = int(indexes[0])
                ex_index = int(indexes[1])
            except ValueError:
                print(var_name)
                exit(8)

            assigning_news = np.random.choice(self.ads_per_category[category_index][ex_index])
            while assigning_news not in result:
                if assigning_news not in result:
                    result[i] = assigning_news
                elif len(self.ads_per_category[category_index][ex_index]) == 1:
                    break
                else:
                    assigning_news = np.random.choice(self.ads_per_category[category_index][ex_index])

        final_result = []
        for elem in result:
            if elem != 0:
                final_result.append(elem)

        for elem in final_result:
            elem.sampled_quality = self.sample_quality(elem, user=None)

        if len(self.other_classes_learners) > 0:
            for elem in final_result:
                other_learners_samples = []
                for learner in self.other_classes_learners:
                    other_learners_samples.append(learner.sample_quality(elem, user=None))
                elem.sampled_quality = elem.sampled_quality / (elem.sampled_quality + sum(other_learners_samples))

        return final_result

    def __de_randomize_LP(self, LP_news_pool, tmp_slots_assignation_probabilities, de_rand_technique):
        """
        Given a randomized solution provided by a LP or an ILP, provide a derandomization, finding then the actual
        allocation of the page. The de-randomization techniques that can be used are "rand_1", "rand_2" and "rand_3".
        :param LP_news_pool: The subset of news used by the linear problem.
        :param tmp_slots_assignation_probabilities: The randomized solution provided by the LP or ILP.
        :param de_rand_technique: the derandomization technique itself.
        :return: A list of news corresponding to the allocation in the page. The order of the news in the list
        correspond to the order of the slots in which the news are allocated.
        """
        result = [0] * self.layout_slots
        tmp_slot_promenances = self.real_slot_promenances.copy()
        feasible_news = [i for i in range(len(LP_news_pool))]
        slot_counter = 0
        allocated_slots = []
        while slot_counter < self.layout_slots:
            if (de_rand_technique == "rand_1") or (de_rand_technique == "rand_3"):
                # Start from the best slot
                target_slot = np.argmax(tmp_slot_promenances)
            else:
                # Start from slot j with probability proportional to j's slot promenance
                tmp_slot_promenance_norm = list(np.array(tmp_slot_promenances) / sum(tmp_slot_promenances))
                target_slot_promenance = np.random.choice(tmp_slot_promenances, p=tmp_slot_promenance_norm)
                target_slot = tmp_slot_promenances.index(target_slot_promenance)

            target_slot_assegnation_probabilities = tmp_slots_assignation_probabilities[int(target_slot)]
            if de_rand_technique == "rand_3":
                for p in range(len(tmp_slots_assignation_probabilities)):
                    if (p not in allocated_slots) and (p != target_slot):
                        target_slot_assegnation_probabilities = \
                            list(np.array(target_slot_assegnation_probabilities) *
                                 (1 - np.array(tmp_slots_assignation_probabilities[p])))
                allocated_slots.append(target_slot)

            # Normalize the vector of the variable assigning to the target slot
            target_slot_assegnation_probabilities_norm = list(np.array(target_slot_assegnation_probabilities) /
                                                              sum(target_slot_assegnation_probabilities))
            # Choose the allocating news with probability proportional to the values of the variables
            selected_news = np.random.choice(feasible_news, p=np.abs(target_slot_assegnation_probabilities_norm))
            # Insert the winner news in the allocation and repeat after removing the variables.
            result[int(target_slot)] = LP_news_pool[selected_news]
            deletion_index = feasible_news.index(selected_news)
            feasible_news.__delitem__(deletion_index)
            for probs in tmp_slots_assignation_probabilities:
                probs.__delitem__(deletion_index)
            tmp_slot_promenances[int(target_slot)] = 0
            slot_counter += 1

        return result


if __name__ == "__main__":

    # We fill the news pool with a bounch of news and ads
    news_pool = []
    ads_pool = []
    times = []
    categories = ["cibo", "gossip", "politic", "scienza", "sport", "tech"]
    real_slot_promenances = [0.9, 0.8, 0.7, 0.8, 0.5, 0.4, 0.5, 0.4, 0.3, 0.1]
    dynamic_refill = True
    result = []
    click_result = []
    diversity_percentage_for_category = 1
    promenance_percentage_value = diversity_percentage_for_category / 100 * sum(real_slot_promenances)
    allocation_diversity_bounds = (promenance_percentage_value, promenance_percentage_value) * 3

    # CREATE A SET OF NEWS TO FEED THE AGENT
    k = 0
    for category in categories:
        for id in range(1, 101):
            news_pool.append(News(news_id=k,
                                  news_name=category + "-" + str(id)))
            k += 1

    # AVERAGE OVER 10 EXPERIMENTS
    for k in tqdm(range(10)):
        # We create a user and set their quality metrics that we want to estimate
        u = SyntheticUser(23, "M", 27)  # A male 27 years old user
        # We manually set its parameters, even if it is not needed
        u.user_quality_measure = [0.3, 0.65, 0.35, 0.3, 0.2, 0.1]
        agent = NewsLearner(categories=categories,
                            real_slot_promenances=real_slot_promenances,
                            allocation_approach="LP",
                            ads_allocation=False,
                            allocation_diversity_bounds=allocation_diversity_bounds)

        agent.fill_news_pool(news_list=news_pool, append=True)
        index = len(news_pool)
        # We simulate 300 interactions for this user
        for i in range(100):
            agent.user_arrival(u, interest_decay=True)

            if dynamic_refill:  # We can fill runtime the agent news pool to keep high the user's interest
                if (i + 1) % 1 == 0:
                    # UPDATE NEWS POOL
                    random.shuffle(agent.news_pool)
                    news_to_be_removed = []
                    for cat in categories:
                        news_count = 0
                        for j in range(len(agent.news_pool)):
                            if agent.news_pool[j].news_category == cat:
                                news_to_be_removed.append(agent.news_pool[j])
                                news_count += 1
                            if news_count == 4:
                                break

                    agent.remove_news_from_pool(news_to_be_removed)

                    for j in range(4):
                        agent.insert_into_news_pool(News(index, "cibo-" + str(index)))
                        index += 1
                        agent.insert_into_news_pool(News(index, "gossip-" + str(index)))
                        index += 1
                        agent.insert_into_news_pool(News(index, "politic-" + str(index)))
                        index += 1
                        agent.insert_into_news_pool(News(index, "scienza-" + str(index)))
                        index += 1
                        agent.insert_into_news_pool(News(index, "sport-" + str(index)))
                        index += 1
                        agent.insert_into_news_pool(News(index, "tech-" + str(index)))
                        index += 1

        result.append(agent.multiple_arms_avg_reward)
        click_result.append(agent.click_per_page)

    plt.plot(np.mean(result, axis=0))
    plt.title("Agent's expected reward")
    plt.xlabel("Interaction")
    plt.ylabel("Expected Reward")
    plt.show()



